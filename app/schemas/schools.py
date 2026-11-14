
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SchoolStatus = Literal["active", "inactive"]


class SchoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    code: str = Field(min_length=1, max_length=50)
    address: str = Field(default="", max_length=100)
    contact: str = Field(default="", max_length=30)
    phone: str = Field(default="", max_length=30)
    status: SchoolStatus = "active"


class SchoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    code: str | None = Field(default=None, min_length=1, max_length=50)
    address: str | None = Field(default=None, max_length=100)
    contact: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, max_length=30)
    status: SchoolStatus | None = None


class SchoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    address: str
    contact: str
    phone: str
    status: SchoolStatus
    created_at: datetime
    updated_at: datetime
