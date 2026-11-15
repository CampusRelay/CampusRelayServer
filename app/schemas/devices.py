from pydantic import BaseModel, Field, field_validator


class DeviceCreate(BaseModel):
    serial_number: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    device_type: str = Field(default="display", min_length=1, max_length=50)
    classroom_id: int = Field(gt=0)

    @field_validator("serial_number", "name", "device_type")
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("字段内容无效")
        return value


class DeviceBind(BaseModel):
    classroom_id: int = Field(gt=0)
