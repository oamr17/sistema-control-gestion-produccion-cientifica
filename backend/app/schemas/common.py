from pydantic import BaseModel


class CareerRead(BaseModel):
    id: int
    name: str
    code: str

    model_config = {"from_attributes": True}


class PeriodRead(BaseModel):
    id: int
    year_label: str
    cycle: int

    model_config = {"from_attributes": True}
