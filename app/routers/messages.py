from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_session
from app.database import get_db
from app.models import (
    Message, MessageDelivery, Device, DeviceBinding, Classroom,
    SchoolMembership, ClassroomMembership,
)
from app.schemas.messages import MessageCreate
from app.websocket.manager import notify_device

router = APIRouter(tags=["Messages"])


def get_classroom_access(current, db, classroom_id):
    user = current["user"]
    school_id = current["session"].active_school_id
    if school_id is None:
        raise HTTPException(status_code=403, detail="请先选择学校")

    classroom = db.scalar(
        select(Classroom).where(
            Classroom.id == classroom_id,
            Classroom.school_id == school_id,
            Classroom.status == "active",
        )
    )
    if classroom is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    membership = db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == school_id,
            SchoolMembership.status == "active",
            SchoolMembership.role.in_(("owner", "admin", "teacher")),
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="无权访问该班级")

    if membership.role == "teacher":
        classroom_membership = db.scalar(
            select(ClassroomMembership).where(
                ClassroomMembership.classroom_id == classroom_id,
                ClassroomMembership.user_id == user.id,
                ClassroomMembership.role == "teacher",
                ClassroomMembership.status == "active",
            )
        )
        if classroom_membership is None:
            raise HTTPException(status_code=403, detail="无权管理该班级")

    return classroom


@router.post("/school/classrooms/{classroom_id}/messages", status_code=201)
def send_classroom_message(
    classroom_id: int,
    data: MessageCreate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    classroom = get_classroom_access(current, db, classroom_id)

    message = Message(
        classroom_id=classroom.id,
        sender_user_id=current["user"].id,
        title=data.title,
        content=data.content,
        message_type=data.message_type,
        status="queued",
    )
    db.add(message)
    db.flush()

    device_ids = db.scalars(
        select(DeviceBinding.device_id)
        .join(Device, Device.id == DeviceBinding.device_id)
        .where(
            DeviceBinding.classroom_id == classroom_id,
            DeviceBinding.unbound_at.is_(None),
            Device.status == "active",
        )
    ).all()

    for device_id in device_ids:
        db.add(MessageDelivery(
            message_id=message.id,
            device_id=device_id,
            status="pending",
        ))

    db.commit()
    db.refresh(message)

    for device_id in device_ids:
        notify_device(device_id, {
            "type": "new_message",
            "message_id": message.id,
            "classroom_id": classroom.id,
            "title": message.title,
            "content": message.content,
            "message_type": message.message_type,
            "created_at": message.created_at.isoformat() if message.created_at else None,
        })

    return {
        "status": "ok",
        "message_id": message.id,
        "classroom_id": classroom_id,
        "delivery_count": len(device_ids),
        "message_status": message.status,
        "created_at": message.created_at,
    }


@router.get("/school/classrooms/{classroom_id}/messages")
def list_classroom_messages(
    classroom_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    get_classroom_access(current, db, classroom_id)

    messages = db.scalars(
        select(Message)
        .where(Message.classroom_id == classroom_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    result = []
    for message in messages:
        counts = db.execute(
            select(MessageDelivery.status, func.count(MessageDelivery.id))
            .where(MessageDelivery.message_id == message.id)
            .group_by(MessageDelivery.status)
        ).all()
        result.append({
            "id": message.id,
            "title": message.title,
            "content": message.content,
            "message_type": message.message_type,
            "status": message.status,
            "created_at": message.created_at,
            "deliveries": {status: count for status, count in counts},
        })

    return {"status": "ok", "total": len(result), "messages": result}


def authenticate_device(device_id, serial_number, db):
    device = db.scalar(
        select(Device).where(
            Device.id == device_id,
            Device.serial_number == serial_number,
            Device.status == "active",
        )
    )
    if device is None:
        raise HTTPException(status_code=401, detail="设备认证失败")

    binding = db.scalar(
        select(DeviceBinding).where(
            DeviceBinding.device_id == device_id,
            DeviceBinding.unbound_at.is_(None),
        )
    )
    if binding is None:
        raise HTTPException(status_code=403, detail="设备未绑定班级")

    return device


@router.get("/devices/{device_id}/messages")
def pull_device_messages(
    device_id: int,
    serial_number: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    device = authenticate_device(device_id, serial_number, db)

    deliveries = db.scalars(
        select(MessageDelivery)
        .join(Message, Message.id == MessageDelivery.message_id)
        .where(
            MessageDelivery.device_id == device.id,
            MessageDelivery.status.in_(("pending", "delivered")),
            Message.scheduled_at.is_(None) | (Message.scheduled_at <= datetime.now(timezone.utc)),
        )
        .order_by(Message.created_at.asc())
        .limit(limit)
    ).all()

    now = datetime.now(timezone.utc)
    result = []
    for delivery in deliveries:
        delivery.status = "delivered"
        delivery.attempts += 1
        delivery.delivered_at = now
        message = db.get(Message, delivery.message_id)
        result.append({
            "delivery_id": delivery.id,
            "message_id": message.id,
            "title": message.title,
            "content": message.content,
            "message_type": message.message_type,
            "created_at": message.created_at,
        })

    db.commit()
    return {"status": "ok", "total": len(result), "messages": result}


@router.post("/devices/{device_id}/messages/{message_id}/ack")
def acknowledge_device_message(
    device_id: int,
    message_id: int,
    serial_number: str = Query(min_length=1, max_length=100),
    db: Session = Depends(get_db),
):
    authenticate_device(device_id, serial_number, db)

    delivery = db.scalar(
        select(MessageDelivery).where(
            MessageDelivery.device_id == device_id,
            MessageDelivery.message_id == message_id,
        )
    )
    if delivery is None:
        raise HTTPException(status_code=404, detail="投递记录不存在")

    if delivery.status != "acknowledged":
        delivery.status = "acknowledged"
        delivery.acknowledged_at = datetime.now(timezone.utc)
        db.commit()

    return {
        "status": "ok",
        "message_id": message_id,
        "delivery_status": delivery.status,
        "acknowledged_at": delivery.acknowledged_at,
    }
