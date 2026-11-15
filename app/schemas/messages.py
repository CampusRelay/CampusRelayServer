from pydantic import BaseModel, Field, field_validator


class MessageCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    content: str = Field(min_length=1, max_length=10000)
    message_type: str = Field(default="text", max_length=30)

    @field_validator("title", "content", "message_type")
    @classmethod
    def strip_text(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value
