from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class MonitoringBaseline(Base):
    """线体监测基线：批准结果的固定预测依据，按生效日分段换版。"""
    __tablename__ = 'monitoring_baselines'
    id: Mapped[int] = mapped_column(primary_key=True)
    line: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    effective_from: Mapped[str] = mapped_column(String(10))
    threshold_pct: Mapped[float] = mapped_column(Double)
    model: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='active')
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('line', 'version'), UniqueConstraint('line', 'effective_from'))


class DailyActual(Base):
    """带修订号的日实测：同一业务日期以更高修订号更正，原修订保留。"""
    __tablename__ = 'daily_actuals'
    id: Mapped[int] = mapped_column(primary_key=True)
    line: Mapped[str] = mapped_column(String(80))
    day: Mapped[str] = mapped_column(String(10))
    revision: Mapped[int] = mapped_column(Integer)
    features: Mapped[dict] = mapped_column(JSON)
    kwh: Mapped[float] = mapped_column(Double)
    received_by: Mapped[str] = mapped_column(String(150))
    received_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('line', 'day', 'revision'),)


class DayJudgment(Base):
    """逐日判断：某次重演（replay_id）下同一输入版本的判定结果。"""
    __tablename__ = 'day_judgments'
    id: Mapped[int] = mapped_column(primary_key=True)
    line: Mapped[str] = mapped_column(String(80))
    day: Mapped[str] = mapped_column(String(10))
    baseline_id: Mapped[int | None] = mapped_column(ForeignKey('monitoring_baselines.id'), nullable=True)
    revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_kwh: Mapped[float | None] = mapped_column(Double, nullable=True)
    predicted_kwh: Mapped[float | None] = mapped_column(Double, nullable=True)
    deviation_pct: Mapped[float | None] = mapped_column(Double, nullable=True)
    state: Mapped[str] = mapped_column(String(20))
    replay_id: Mapped[str] = mapped_column(String(32))
    judged_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('line', 'day'),)


class AnomalyTicket(Base):
    """异常单：pending 待评估 / open 异常 / recovered 恢复 / revoked 撤销。"""
    __tablename__ = 'anomaly_tickets'
    id: Mapped[int] = mapped_column(primary_key=True)
    line: Mapped[str] = mapped_column(String(80))
    baseline_id: Mapped[int] = mapped_column(ForeignKey('monitoring_baselines.id'))
    start_day: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    opened_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_exceed_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    closed_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    close_reason: Mapped[str] = mapped_column(String(40), default='')
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    replay_id: Mapped[str] = mapped_column(String(32), default='')
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)
    __table_args__ = (UniqueConstraint('line', 'baseline_id', 'start_day'),)


class AnomalyEvent(Base):
    """异常单事件：状态变迁与人工处置记录，撤销后仍保留。"""
    __tablename__ = 'anomaly_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey('anomaly_tickets.id'))
    actor: Mapped[str] = mapped_column(String(150))
    kind: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(String(500), default='')
    replay_id: Mapped[str] = mapped_column(String(32), default='')
    at: Mapped[float] = mapped_column(Double)


class LineState(Base):
    """每线体一行的重演互斥锁与水位记录。"""
    __tablename__ = 'line_states'
    line: Mapped[str] = mapped_column(String(80), primary_key=True)
    updated_at: Mapped[float] = mapped_column(Double)
