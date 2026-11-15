import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device, DeviceBinding
from app.websocket.manager import connect, disconnect

router = APIRouter(tags=["Device WebSocket"])


@router.websocket("/devices/{device_id}/ws")
async def device_websocket(
    websocket: WebSocket,
    device_id: int,
    serial_number: str = Query(min_length=1, max_length=100),
    db: Session = Depends(get_db),
):
    device = db.scalar(
        select(Device).where(
            Device.id == device_id,
            Device.serial_number == serial_number,
            Device.status == "active",
        )
    )

    if device is None:
        await websocket.close(code=1008, reason="Device authentication failed")
        return

    binding = db.scalar(
        select(DeviceBinding).where(
            DeviceBinding.device_id == device_id,
            DeviceBinding.unbound_at.is_(None),
        )
    )
    if binding is None:
        await websocket.close(code=1008, reason="Device is not bound")
        return

    await websocket.accept()
    connect(device_id, websocket, asyncio.get_running_loop())

    try:
        await websocket.send_json({
            "type": "connected",
            "device_id": device_id,
        })

        while True:
            # 客户端可发送 ping 保持连接；消息内容由客户端自行处理
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        disconnect(device_id, websocket)
