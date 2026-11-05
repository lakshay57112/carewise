from typing import Optional

from pydantic import BaseModel, Field


class Medicine(BaseModel):
    name: str
    dose: str = ""
    instructions: str = ""
    times_per_day: int = 0
    timing: list[str] = Field(default_factory=list)  # morning / afternoon / evening / night
    food: str = "any"  # before / after / any
    duration_days: Optional[int] = None


class FollowUp(BaseModel):
    date: str = ""
    note: str = ""


class Discharge(BaseModel):
    patient_name: str = ""
    diagnosis: str = ""
    medicines: list[Medicine] = Field(default_factory=list)
    diet: list[str] = Field(default_factory=list)
    activity: list[str] = Field(default_factory=list)
    follow_up: FollowUp = Field(default_factory=FollowUp)
    warning_signs: list[str] = Field(default_factory=list)
    extractor: str = "rules"  # which reader produced this: "rules" or "gemini"


class Flag(BaseModel):
    id: str
    field: str
    value: str
    issue: str
