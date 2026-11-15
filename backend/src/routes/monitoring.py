from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.config.database import get_db
from src.models import MonitoringBaseline, DailyActual, DayJudgment, AnomalyTicket, AnomalyEvent
from src.schemas.monitoring import BaselineInput, ActualsInput, HandleInput
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import monitoring
from src.services.audit import record
from src.utils.serialization import baseline_dict, actual_dict, judgment_dict, ticket_dict, event_dict

router = APIRouter(prefix='/monitoring')


def _commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '该线体正在并发重演或换版，请重试；幂等约束保证不重复开单') from None


@router.get('/lines')
def lines(db=Depends(get_db), u=Depends(current_user)):
    return monitoring.lines_summary(db)


@router.get('/baselines')
def baselines(line: str = '', db=Depends(get_db), u=Depends(current_user)):
    q = select(MonitoringBaseline).order_by(MonitoringBaseline.line, MonitoringBaseline.version)
    if line:
        q = q.where(MonitoringBaseline.line == line)
    return [baseline_dict(b) for b in db.scalars(q)]


@router.post('/baselines', status_code=201)
def create_baseline(data: BaselineInput, db=Depends(get_db), u=Depends(admin_only)):
    b = monitoring.create_baseline(db, data, u)
    record(db, u.email, 'baseline.create', {'id': b.id, 'line': b.line, 'version': b.version})
    _commit(db)
    return baseline_dict(b)


@router.get('/actuals')
def actuals(line: str, day_from: str = '', day_to: str = '', db=Depends(get_db), u=Depends(current_user)):
    q = select(DailyActual).where(DailyActual.line == line)
    if day_from:
        q = q.where(DailyActual.day >= day_from)
    if day_to:
        q = q.where(DailyActual.day <= day_to)
    return [actual_dict(a) for a in db.scalars(q.order_by(DailyActual.day.desc(), DailyActual.revision.desc()).limit(200))]


@router.post('/actuals', status_code=201)
def ingest(data: ActualsInput, db=Depends(get_db), u=Depends(can_write)):
    result = monitoring.ingest(db, data.records, u.email)
    record(db, u.email, 'actuals.ingest', result)
    _commit(db)
    return result


@router.get('/days')
def days(line: str, state: str = '', db=Depends(get_db), u=Depends(current_user)):
    q = select(DayJudgment).where(DayJudgment.line == line)
    if state:
        q = q.where(DayJudgment.state == state)
    return [judgment_dict(j) for j in db.scalars(q.order_by(DayJudgment.day.desc()).limit(120))]


@router.get('/tickets')
def tickets(line: str = '', status: str = '', db=Depends(get_db), u=Depends(current_user)):
    q = select(AnomalyTicket).order_by(AnomalyTicket.start_day.desc(), AnomalyTicket.id.desc()).limit(100)
    if line:
        q = q.where(AnomalyTicket.line == line)
    if status:
        q = q.where(AnomalyTicket.status == status)
    return [ticket_dict(t) for t in db.scalars(q)]


@router.get('/tickets/{id}')
def ticket_detail(id: int, db=Depends(get_db), u=Depends(current_user)):
    t = db.get(AnomalyTicket, id)
    if not t:
        raise HTTPException(404, '异常单不存在')
    events = db.scalars(select(AnomalyEvent).where(AnomalyEvent.ticket_id == id).order_by(AnomalyEvent.at, AnomalyEvent.id)).all()
    return {**ticket_dict(t), 'events': [event_dict(e) for e in events]}


@router.post('/tickets/{id}/handles', status_code=201)
def handle(id: int, data: HandleInput, db=Depends(get_db), u=Depends(can_write)):
    t = db.get(AnomalyTicket, id)
    if not t:
        raise HTTPException(404, '异常单不存在')
    monitoring.add_handle(db, t, u, data.note.strip())
    record(db, u.email, 'ticket.handle', {'id': id})
    _commit(db)
    return ticket_dict(t)


@router.post('/tickets/{id}/revoke')
def revoke(id: int, data: HandleInput, db=Depends(get_db), u=Depends(admin_only)):
    t = db.get(AnomalyTicket, id)
    if not t:
        raise HTTPException(404, '异常单不存在')
    monitoring.revoke(db, t, u, data.note.strip())
    record(db, u.email, 'ticket.revoke', {'id': id})
    _commit(db)
    return ticket_dict(t)
