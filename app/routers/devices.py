from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_session
from app.database import get_db
from app.models import Device, DeviceBinding, Classroom, SchoolMembership
from app.schemas.devices import DeviceCreate, DeviceBind

router = APIRouter(tags=["School Devices"])


def get_school_admin(current, db):
    user = current["user"]
    school_id = current["session"].active_school_id

    if school_id is None:
        raise HTTPException(status_code=403, detail="请先选择学校")

    membership = db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == school_id,
            SchoolMembership.status == "active",
            SchoolMembership.role.in_(("owner", "admin")),
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="需要学校管理员权限")

    return user, school_id


def get_school_classroom(db, classroom_id, school_id):
    classroom = db.scalar(
        select(Classroom).where(
            Classroom.id == classroom_id,
            Classroom.school_id == school_id,
            Classroom.status == "active",
        )
    )
    if classroom is None:
        raise HTTPException(status_code=404, detail="班级不存在或已停用")
    return classroom


@router.get("/school/devices")
def list_devices(
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school_id = get_school_admin(current, db)

    rows = db.execute(
        select(Device, DeviceBinding, Classroom)
        .join(
            DeviceBinding,
            DeviceBinding.device_id == Device.id,
        )
        .join(
            Classroom,
            Classroom.id == DeviceBinding.classroom_id,
        )
        .where(
            Classroom.school_id == school_id,
            DeviceBinding.unbound_at.is_(None),
        )
        .order_by(Device.created_at.desc())
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "devices": [
            {
                "id": device.id,
                "serial_number": device.serial_number,
                "name": device.name,
                "device_type": device.device_type,
                "status": device.status,
                "classroom_id": classroom.id,
                "classroom_name": classroom.name,
                "bound_at": binding.bound_at,
                "last_seen_at": device.last_seen_at,
            }
            for device, binding, classroom in rows
        ],
    }


@router.post("/school/devices", status_code=201)
def register_device(
    data: DeviceCreate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user, school_id = get_school_admin(current, db)

    get_school_classroom(db, data.classroom_id, school_id)

    existing = db.scalar(
        select(Device).where(Device.serial_number == data.serial_number)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="设备序列号已存在")

    try:
        device = Device(
            serial_number=data.serial_number,
            name=data.name,
            device_type=data.device_type,
            status="active",
        )
        db.add(device)
        db.flush()

        db.add(
            DeviceBinding(
                device_id=device.id,
                classroom_id=data.classroom_id,
                bound_by_user_id=user.id,
            )
        )
        db.commit()
        db.refresh(device)

        return {
            "status": "ok",
            "device_id": device.id,
            "serial_number": device.serial_number,
            "classroom_id": data.classroom_id,
        }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="设备序列号已存在或绑定冲突")


@router.post("/school/devices/{device_id}/bind")
def bind_device(
    device_id: int,
    data: DeviceBind,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user, school_id = get_school_admin(current, db)

    get_school_classroom(db, data.classroom_id, school_id)

    device = db.scalar(
        select(Device).where(Device.id == device_id).with_for_update()
    )
    if device is None:
        raise HTTPException(status_code=404, detail="设备不存在")

    active_binding = db.scalar(
        select(DeviceBinding).where(
            DeviceBinding.device_id == device_id,
            DeviceBinding.unbound_at.is_(None),
        )
    )
    if active_binding is not None:
        raise HTTPException(status_code=409, detail="设备已经绑定班级")

    db.add(
        DeviceBinding(
            device_id=device_id,
            classroom_id=data.classroom_id,
            bound_by_user_id=user.id,
        )
    )
    device.status = "active"

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="设备绑定冲突")

    return {"status": "ok", "device_id": device_id, "classroom_id": data.classroom_id}


@router.post("/school/devices/{device_id}/unbind")
def unbind_device(
    device_id: int,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school_id = get_school_admin(current, db)

    binding = db.scalar(
        select(DeviceBinding)
        .join(Classroom, Classroom.id == DeviceBinding.classroom_id)
        .where(
            DeviceBinding.device_id == device_id,
            DeviceBinding.unbound_at.is_(None),
            Classroom.school_id == school_id,
        )
        .with_for_update()
    )
    if binding is None:
        raise HTTPException(status_code=404, detail="本校未找到该设备的有效绑定")

    binding.unbound_at = datetime.now(timezone.utc)
    db.commit()

    return {"status": "ok", "message": "设备已解除绑定"}
