import re

from pydantic import BaseModel, Field, field_validator


class ClassroomCreationRequestCreate(BaseModel):
    class_name: str = Field(min_length=1, max_length=80)

    @field_validator("class_name")
    @classmethod
    def clean_class_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("班级名称不能为空或包含非法字符")
        return value


class ClassroomCreationRequestReject(BaseModel):
    reason: str = Field(default="管理员拒绝班级创建申请", max_length=200)


class ClassroomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    grade: str | None = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value is not None:
            value = value.strip()
            if not value or any(ord(char) < 32 for char in value):
                raise ValueError("班级名称无效")
        return value

    @field_validator("grade")
    @classmethod
    def clean_grade(cls, value):
        if value is not None:
            value = value.strip()
            if not value:
                return None
        return value
