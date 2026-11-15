import time, json, math, random
from datetime import date, timedelta
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, MonitoringBaseline, AnomalyTicket
from src.services.business import metadata, seed_records, analyze
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum

MONITOR_LINE = '冲压P01'


def monitoring_rows():
    """基线生效后的日实测：10-20 起 5 日 +14%（开后恢复），11-02 起 3 日 +12%（后被更正撤销），11-11 起 3 日 +15%（异常中）。"""
    rng = random.Random(20261008)
    start = date(2026, 10, 8)
    boosts = {}
    for i in range(5):
        boosts[str(date(2026, 10, 20) + timedelta(days=i))] = 1.14
    for i in range(3):
        boosts[str(date(2026, 11, 2) + timedelta(days=i))] = 1.12
        boosts[str(date(2026, 11, 11) + timedelta(days=i))] = 1.15
    rows = []
    for i in range(38):  # 2026-10-08 .. 2026-11-14
        day = str(start + timedelta(days=i))
        g = 160 + i  # 延续 seed_records 的季节项
        units = round(760 + 180 * math.sin(g / 11) + rng.gauss(0, 85), 1)
        run_hours = round(6.4 + units / 140 + rng.gauss(0, 0.65), 2)
        temp_c = round(24 + 7 * math.sin(g / 33) + rng.gauss(0, 1.4), 2)
        idle_hours = round(rng.uniform(0.3, 3.6), 2)
        kwh_normal = round(125 + units * 0.46 + run_hours * 24 + temp_c * 4.1 + idle_hours * 38 + rng.gauss(0, 13), 2)
        rows.append({'day': day, 'units': units, 'run_hours': run_hours, 'temp_c': temp_c,
                     'idle_hours': idle_hours, 'kwh_normal': kwh_normal,
                     'kwh': round(kwh_normal * boosts.get(day, 1.0), 2)})
    return rows


def seed_monitoring(db, m):
    """幂等 seed：监测基线 v1、日实测、一次更正引发的误报撤销，以及人工处置记录。"""
    from src.schemas.monitoring import BaselineInput, ActualRecord
    from src.services import monitoring
    if db.scalar(select(MonitoringBaseline).where(MonitoringBaseline.line == MONITOR_LINE)):
        return
    job = db.scalar(select(Job).where(Job.name == '基线复核 · ' + m['name']))
    if not job or not job.approved or not (job.result or {}).get('model'):
        return
    admin = db.scalar(select(User).where(User.role == 'admin'))
    monitoring.create_baseline(db, BaselineInput(job_id=job.id, line=MONITOR_LINE, effective_from='2026-10-08', threshold_pct=8), admin)
    rows = monitoring_rows()
    monitoring.ingest(db, [ActualRecord(line=MONITOR_LINE, day=r['day'], revision=1, units=r['units'], run_hours=r['run_hours'],
                                        temp_c=r['temp_c'], idle_hours=r['idle_hours'], kwh=r['kwh']) for r in rows], 'seed')
    # 迟到更正：11-03 修订 2 回到正常电耗，重演后 11-04 开具的异常单按误报撤销
    fixed = next(r for r in rows if r['day'] == '2026-11-03')
    monitoring.ingest(db, [ActualRecord(line=MONITOR_LINE, day='2026-11-03', revision=2, units=fixed['units'], run_hours=fixed['run_hours'],
                                        temp_c=fixed['temp_c'], idle_hours=fixed['idle_hours'], kwh=fixed['kwh_normal'])], 'seed')
    ticket = db.scalar(select(AnomalyTicket).where(AnomalyTicket.line == MONITOR_LINE, AnomalyTicket.status == 'open'))
    if ticket:
        monitoring.add_handle(db, ticket, admin, '已通知设备组检查冲压线润滑与模具状态，等待连续两日恢复确认。')
    db.add(Audit(actor='seed', action='monitoring.baseline', detail={'line': MONITOR_LINE}, at=time.time()))


def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            result=analyze(d.records,params)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            admin=db.scalar(select(User).where(User.role=='admin'))
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id},at=time.time()))
        seed_monitoring(db, m)
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,MonitoringBaseline,AnomalyTicket]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
