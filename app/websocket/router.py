from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket.manager import manager

router = APIRouter()


@router.websocket("/ws/classrooms/{classroom_id}")
async def classroom_websocket(websocket: WebSocket, classroom_id: int):
    # 暂时只提供实时通知通道；消息内容仍通过已认证的设备接口拉取。
    await manager.connect(classroom_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(classroom_id, websocket)
    except Exception:
        manager.disconnect(classroom_id, websocket)
