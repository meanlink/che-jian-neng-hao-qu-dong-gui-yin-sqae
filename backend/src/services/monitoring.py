"""线体监测：基线版本、带修订号的日实测、按业务日期重演与异常单状态机。

判定规则（与业务约定一一对应）：
- 已批准结果设为监测基线，预测依据（入选特征、原始单位系数、截距）随版本冻结；
- 连续 3 日超出可设偏差阈值才开异常单，连续 2 日回到阈值内才关闭；
- 缺日不算恢复，也不计入连续超出，两种连续计数都在缺日中断；
- 迟到记录或更正（更高修订号）按业务日期重演受影响区间，误报撤销但保留人工处置记录；
- 基线换版按生效日分段，跨版不拼连续天数；
- 日判断与异常起止共用同一 replay_id，对应同一输入版本。
"""
import math
import time
import uuid
from datetime import date, timedelta
from fastapi import HTTPException
from sqlalchemy import select
from src.models import (Job, MonitoringBaseline, DailyActual, DayJudgment,
                        AnomalyTicket, AnomalyEvent, LineState)
from src.services.business import FEATURES
from src.utils.serialization import baseline_dict, judgment_dict

FEATURE_KEYS = tuple(FEATURES)
OPEN_STREAK = 3     # 连续三日超出才开异常
RECOVER_STREAK = 2  # 连续两日恢复才关闭
BOUNDS = {'units': (0, 10_000_000), 'run_hours': (0, 24), 'temp_c': (-100, 100),
          'idle_hours': (0, 24), 'kwh': (0, 1_000_000_000)}
LABELS = {'units': '日产量', 'run_hours': '运行小时', 'temp_c': '环境温度', 'idle_hours': '待机小时', 'kwh': '日电耗'}


def _iso(value, name='业务日期'):
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        raise HTTPException(422, f'{name}须为ISO日期') from None


def _bounded(key, value):
    low, high = BOUNDS[key]
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f'{LABELS[key]}必须为数值') from None
    if not math.isfinite(number) or not low <= number <= high:
        raise HTTPException(422, f'{LABELS[key]}必须介于{low}和{high}')
    return number


def predict(model, features):
    """用基线冻结的预测依据计算当日预测电耗。"""
    return float(model['intercept']) + sum(float(model['coefficients'][key]) * float(features[key]) for key in model['features'])


def create_baseline(db, data, user):
    """把已批准结果设为线体监测基线；换版按生效日分段并触发重演。"""
    job = db.get(Job, data.job_id)
    if not job or job.status != 'completed' or not job.approved:
        raise HTTPException(409, '仅已批准的分析结果可以设为监测基线')
    model = (job.result or {}).get('model') or {}
    if not model.get('features') or 'intercept' not in model or not all(key in model.get('coefficients', {}) for key in model.get('features', [])):
        raise HTTPException(409, '该结果缺少固定预测依据，请用当前算法重新计算并批准后再设基线')
    line = data.line.strip()
    if len(line) < 2:
        raise HTTPException(422, '线体名称去除空格后至少需要两个字符')
    effective = _iso(data.effective_from, '生效日')
    threshold = float(data.threshold_pct)
    if not math.isfinite(threshold) or not 0 < threshold <= 50:
        raise HTTPException(422, '偏差阈值必须介于0和50%之间')
    versions = db.scalars(select(MonitoringBaseline).where(MonitoringBaseline.line == line).order_by(MonitoringBaseline.version)).all()
    if versions and effective <= _iso(versions[-1].effective_from, '生效日'):
        raise HTTPException(409, '新基线生效日必须晚于该线体既有版本的生效日，跨版不拼连续天数')
    for old in versions:
        old.status = 'superseded'
    baseline = MonitoringBaseline(
        line=line, version=(versions[-1].version if versions else 0) + 1, job_id=job.id,
        effective_from=str(effective), threshold_pct=threshold,
        model={'features': list(model['features']), 'coefficients': dict(model['coefficients']),
               'intercept': float(model['intercept']), 'job_id': job.id, 'job_name': job.name},
        status='active', created_by=user.id, created_at=time.time())
    db.add(baseline)
    db.flush()
    replay(db, line, effective, actor='baseline')
    return baseline


def ingest(db, records, actor):
    """接入带修订号的日实测；同键同内容幂等跳过，迟到或更正触发受影响区间重演。"""
    touched = {}
    created = 0
    for item in records:
        line = item.line.strip()
        if len(line) < 2:
            raise HTTPException(422, '线体名称去除空格后至少需要两个字符')
        day = _iso(item.day)
        revision = int(item.revision)
        if revision < 1:
            raise HTTPException(422, '修订号必须为不小于1的整数')
        features = {key: _bounded(key, getattr(item, key)) for key in FEATURE_KEYS}
        kwh = _bounded('kwh', item.kwh)
        existing = db.scalars(select(DailyActual).where(DailyActual.line == line, DailyActual.day == str(day), DailyActual.revision == revision)).first()
        if existing:
            if all(existing.features[key] == features[key] for key in FEATURE_KEYS) and existing.kwh == kwh:
                continue  # 中断恢复后的重试或重复补报：幂等跳过，不重复开单
            raise HTTPException(409, f'{line} {day} 的修订号 {revision} 已存在且内容不同，请用更高的修订号更正')
        db.add(DailyActual(line=line, day=str(day), revision=revision, features=features, kwh=kwh,
                           received_by=actor, received_at=time.time()))
        created += 1
        touched.setdefault(line, set()).add(day)
    db.flush()
    for line, days in touched.items():
        replay(db, line, min(days), actor=actor)
    return {'ingested': created, 'skipped': len(records) - created, 'lines': sorted(touched)}


def replay(db, line, from_day, actor):
    """按业务日期重演受影响区间：重算逐日判断，并以同一 replay_id 推进异常单。

    整个重演在调用方的事务内执行；line_states 行级锁串行化同一线体的并发补报与换版，
    中断恢复后重放同一输入得到同一结果，不重复开单。
    """
    lock = db.scalars(select(LineState).where(LineState.line == line).with_for_update()).first()
    if not lock:
        lock = LineState(line=line, updated_at=time.time())
        db.add(lock)
        db.flush()
    replay_id = uuid.uuid4().hex
    baselines = db.scalars(select(MonitoringBaseline).where(MonitoringBaseline.line == line).order_by(MonitoringBaseline.effective_from, MonitoringBaseline.version)).all()
    actuals = {}
    for row in db.scalars(select(DailyActual).where(DailyActual.line == line)):
        current = actuals.get(row.day)
        if not current or row.revision > current.revision:
            actuals[row.day] = row
    if not actuals:
        return replay_id
    max_day = max(actuals)
    last_judged = db.scalars(select(DayJudgment.day).where(DayJudgment.line == line).order_by(DayJudgment.day.desc()).limit(1)).first()
    start = str(from_day)
    if last_judged:
        gap_start = str(_iso(last_judged) + timedelta(days=1))
        if gap_start < start:
            start = gap_start  # 补出演示区间内的缺日判断
    existing = {j.day: j for j in db.scalars(select(DayJudgment).where(DayJudgment.line == line, DayJudgment.day >= start))}
    cursor, end = date.fromisoformat(start), date.fromisoformat(max_day)
    while cursor <= end:
        key = str(cursor)
        baseline = _effective(baselines, cursor)
        actual = actuals.get(key)
        state, predicted, deviation = 'missing', None, None
        if not baseline:
            state = 'pending'  # 无基线覆盖，待评估
        elif actual:
            predicted = round(predict(baseline.model, actual.features), 3)
            deviation = round((actual.kwh - predicted) / predicted * 100, 3) if predicted > 0 else None
            state = 'exceed' if deviation is None or abs(deviation) > baseline.threshold_pct else 'normal'
        values = {'baseline_id': baseline.id if baseline else None,
                  'revision': actual.revision if actual else None,
                  'actual_kwh': actual.kwh if actual else None,
                  'predicted_kwh': predicted, 'deviation_pct': deviation, 'state': state,
                  'replay_id': replay_id, 'judged_at': time.time()}
        judgment = existing.get(key)
        if judgment:
            for field, value in values.items():
                setattr(judgment, field, value)
        else:
            db.add(DayJudgment(line=line, day=key, **values))
        cursor += timedelta(days=1)
    db.flush()
    judgments = db.scalars(select(DayJudgment).where(DayJudgment.line == line).order_by(DayJudgment.day)).all()
    _reconcile(db, line, judgments, replay_id, actor)
    lock.updated_at = time.time()
    return replay_id


def _effective(baselines, day):
    """某业务日期生效的基线版本：按生效日分段，取不大于当日的最新版本。"""
    chosen = None
    for baseline in baselines:
        if baseline.effective_from <= str(day):
            chosen = baseline
        else:
            break
    return chosen


def _snapshot(j):
    return {'day': j.day, 'revision': j.revision, 'actual_kwh': j.actual_kwh,
            'predicted_kwh': j.predicted_kwh, 'deviation_pct': j.deviation_pct}


def _derive(judgments):
    """由逐日判断推导异常单时间线（纯函数）。换版分段，跨版不拼连续天数。"""
    spells, current = [], None
    streak = recover = 0
    prev_baseline = prev_day = None
    for j in judgments:
        if j.baseline_id != prev_baseline:  # 基线换版边界：旧版单据结案，连续天数清零
            if current:
                _close(current, 'recovered' if current['opened_day'] else 'revoked', 'baseline_switch', prev_day)
                spells.append(current)
                current = None
            streak = recover = 0
            prev_baseline = j.baseline_id
        if j.state == 'exceed':
            streak += 1
            recover = 0
            if not current:
                current = {'baseline_id': j.baseline_id, 'start_day': j.day, 'opened_day': None,
                           'last_exceed_day': j.day, 'status': 'pending', 'closed_day': None,
                           'close_reason': '', 'open_basis': [], 'close_basis': []}
            current['last_exceed_day'] = j.day
            if len(current['open_basis']) < OPEN_STREAK:
                current['open_basis'].append(_snapshot(j))
            if streak >= OPEN_STREAK and not current['opened_day']:
                current['opened_day'] = j.day
                current['status'] = 'open'
        elif j.state == 'normal':
            if current and current['status'] == 'open':
                recover += 1
                if len(current['close_basis']) < RECOVER_STREAK:
                    current['close_basis'].append(_snapshot(j))
                if recover >= RECOVER_STREAK:
                    _close(current, 'recovered', 'auto_recovered', j.day)
                    spells.append(current)
                    current = None
            elif current:  # 待评估期间连续超出中断，未构成异常
                _close(current, 'revoked', 'streak_broken', j.day)
                spells.append(current)
                current = None
            streak = 0
        else:  # missing / 无基线：缺日不算恢复，连续计数中断
            if current and current['status'] != 'open':
                _close(current, 'revoked', 'streak_broken', j.day)
                spells.append(current)
                current = None
            streak = recover = 0
        prev_day = j.day
    if current:
        spells.append(current)
    return spells


def _close(spell, status, reason, day):
    spell['status'] = status
    spell['close_reason'] = reason
    spell['closed_day'] = day


def _reconcile(db, line, judgments, replay_id, actor):
    """把推导时间线与既有异常单对齐：匹配更新、缺失撤销、新增登记，全程幂等。"""
    spells = _derive(judgments)
    tickets = db.scalars(select(AnomalyTicket).where(AnomalyTicket.line == line)).all()
    by_key = {(t.baseline_id, t.start_day): t for t in tickets}
    seen = set()
    for spell in spells:
        key = (spell['baseline_id'], spell['start_day'])
        seen.add(key)
        evidence = {'open_basis': spell['open_basis'], 'close_basis': spell['close_basis'], 'replay_id': replay_id}
        ticket = by_key.get(key)
        if not ticket:
            ticket = AnomalyTicket(line=line, baseline_id=spell['baseline_id'], start_day=spell['start_day'],
                                   status=spell['status'], opened_day=spell['opened_day'],
                                   last_exceed_day=spell['last_exceed_day'], closed_day=spell['closed_day'],
                                   close_reason=spell['close_reason'], evidence=evidence, replay_id=replay_id,
                                   created_at=time.time(), updated_at=time.time())
            db.add(ticket)
            db.flush()
            _event(db, ticket, actor, 'created', f"重演登记：{spell['start_day']} 起连续超出，当前状态 {spell['status']}", replay_id)
            continue
        if ticket.close_reason == 'manual_revoked':
            continue  # 人工撤销为最终处置，保留人工处置记录，不随重演翻案
        _apply(ticket, spell, evidence, replay_id, db, actor)
    for key, ticket in by_key.items():
        if key in seen or ticket.close_reason == 'manual_revoked':
            continue
        if ticket.status in ('pending', 'open', 'recovered'):
            ticket.status = 'revoked'
            ticket.close_reason = 'replay_revoked'
            ticket.replay_id = replay_id
            ticket.updated_at = time.time()
            _event(db, ticket, actor, 'revoked', '迟到记录或更正重演后，开单依据不再成立，撤销误报', replay_id)


def _apply(ticket, spell, evidence, replay_id, db, actor):
    old, new = ticket.status, spell['status']
    reason = spell['close_reason']
    if new == 'revoked' and (ticket.opened_day or old in ('open', 'recovered')):
        reason = 'replay_revoked'  # 曾开单，重演后依据不成立，按误报撤销
    if old != new:
        notes = {'opened': '连续三日超出阈值，开具异常单', 'recovered': '连续两日回到阈值内，恢复关闭',
                 'revoked': '重演后判定为误报或未达开单条件，撤销', 'reopened': '更正数据推翻了恢复结论，重新打开'}
        kind = {('pending', 'open'): 'opened', ('open', 'recovered'): 'recovered', ('pending', 'recovered'): 'recovered',
                ('recovered', 'open'): 'reopened', ('pending', 'revoked'): 'revoked', ('open', 'revoked'): 'revoked',
                ('recovered', 'revoked'): 'revoked'}.get((old, new))
        if kind:
            _event(db, ticket, actor, kind, notes[kind], replay_id)
    elif old in ('recovered', 'revoked') and (ticket.closed_day != spell['closed_day'] or ticket.close_reason != reason):
        _event(db, ticket, actor, 'adjusted', '重演更新了异常起止日期或关闭原因', replay_id)
    ticket.status = new
    ticket.opened_day = spell['opened_day'] or ticket.opened_day
    ticket.last_exceed_day = spell['last_exceed_day']
    ticket.closed_day = spell['closed_day']
    ticket.close_reason = reason
    ticket.evidence = evidence
    ticket.replay_id = replay_id
    ticket.updated_at = time.time()


def _event(db, ticket, actor, kind, note, replay_id):
    db.add(AnomalyEvent(ticket_id=ticket.id, actor=actor, kind=kind, note=note[:490],
                        replay_id=replay_id or '', at=time.time()))


def add_handle(db, ticket, user, note):
    """人工处置记录：附加在异常单上，撤销后仍保留。"""
    _event(db, ticket, user.email, 'note', note, ticket.replay_id)
    ticket.updated_at = time.time()


def revoke(db, ticket, user, note):
    """人工撤销误报；单据与人工处置记录保留可查。"""
    if ticket.status not in ('pending', 'open'):
        raise HTTPException(409, '仅待评估或异常中的单据可以撤销')
    ticket.status = 'revoked'
    ticket.close_reason = 'manual_revoked'
    ticket.closed_day = None
    ticket.updated_at = time.time()
    _event(db, ticket, user.email, 'manual_revoked', note, ticket.replay_id)


def lines_summary(db):
    """各线体监测概览：当前基线、各状态单据数、最新逐日判断。"""
    names = set(db.scalars(select(MonitoringBaseline.line)).all())
    names |= set(db.scalars(select(DailyActual.line)).all())
    out = []
    for line in sorted(names):
        baselines = db.scalars(select(MonitoringBaseline).where(MonitoringBaseline.line == line).order_by(MonitoringBaseline.version)).all()
        active = next((b for b in reversed(baselines) if b.status == 'active'), None)
        counts = {s: 0 for s in ('pending', 'open', 'recovered', 'revoked')}
        for t in db.scalars(select(AnomalyTicket).where(AnomalyTicket.line == line)):
            counts[t.status] = counts.get(t.status, 0) + 1
        latest = db.scalars(select(DayJudgment).where(DayJudgment.line == line).order_by(DayJudgment.day.desc()).limit(1)).first()
        out.append({'line': line, 'versions': len(baselines), 'baseline': baseline_dict(active) if active else None,
                    'counts': counts, 'latest': judgment_dict(latest) if latest else None})
    return out
