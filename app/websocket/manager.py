import asyncio
from collections import defaultdict

from fastapi import WebSocket


_connections: dict[int, set[WebSocket]] = defaultdict(set)
_event_loops: dict[int, asyncio.AbstractEventLoop] = {}


def connect(
    device_id: int,
    websocket: WebSocket,
    loop: asyncio.AbstractEventLoop,
):
    _connections[device_id].add(websocket)
    _event_loops[device_id] = loop


def disconnect(device_id: int, websocket: WebSocket):
    connections = _connections.get(device_id)
    if connections is None:
        return

    connections.discard(websocket)

    if not connections:
        _connections.pop(device_id, None)
        _event_loops.pop(device_id, None)


async def _send_to_device(device_id: int, data: dict):
    connections = list(_connections.get(device_id, set()))

    for websocket in connections:
        try:
            await websocket.send_json(data)
        except Exception:
            disconnect(device_id, websocket)


def notify_device(device_id: int, data: dict):
    loop = _event_loops.get(device_id)

    if loop is None or loop.is_closed():
        return

    try:
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(_send_to_device(device_id, data))
        )
    except RuntimeError:
        pass


def connected_device_count() -> int:
    return len(_connections)
