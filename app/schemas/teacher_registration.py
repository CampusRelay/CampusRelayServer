import re

from pydantic import BaseModel, Field, field_validator


class TeacherRegistrationCreate(BaseModel):
    school_name: str = Field(min_length=1, max_length=100)
    join_code: str = Field(min_length=6, max_length=6)
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{3,32}", value):
            raise ValueError("账号格式不正确")
        return value

    @field_validator("school_name", "display_name")
    @classmethod
    def clean_names(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("名称不能为空或包含非法字符")
        return value


class TeacherRegistrationReject(BaseModel):
    reason: str = Field(
        default="管理员拒绝注册",
        max_length=200,
    )
