from pydantic import BaseModel, Field


class BaselineInput(BaseModel):
    job_id: int
    line: str = Field(min_length=2, max_length=80)
    effective_from: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    threshold_pct: float = Field(gt=0, le=50)


class ActualRecord(BaseModel):
    line: str = Field(min_length=2, max_length=80)
    day: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    revision: int = Field(ge=1, le=99999)
    units: float
    run_hours: float
    temp_c: float
    idle_hours: float
    kwh: float


class ActualsInput(BaseModel):
    records: list[ActualRecord] = Field(min_length=1, max_length=500)


class HandleInput(BaseModel):
    note: str = Field(min_length=2, max_length=500)
