import asyncio
from unittest.mock import AsyncMock

from app.websocket import manager as websocket_manager


class FakeWebSocket:
    def __init__(self):
        self.send_json = AsyncMock()


def test_websocket_connect_and_disconnect():
    async def run():
        device_id = 987654
        websocket = FakeWebSocket()
        loop = asyncio.get_running_loop()

        websocket_manager.connect(device_id, websocket, loop)

        assert device_id in websocket_manager._connections
        assert websocket in websocket_manager._connections[device_id]

        websocket_manager.disconnect(device_id, websocket)
        assert device_id not in websocket_manager._connections

    asyncio.run(run())


def test_websocket_notify_device():
    async def run():
        device_id = 987655
        websocket = FakeWebSocket()
        loop = asyncio.get_running_loop()

        websocket_manager.connect(device_id, websocket, loop)

        try:
            websocket_manager.notify_device(
                device_id,
                {"type": "new_message", "message_id": 123},
            )
            await asyncio.sleep(0.05)

            websocket.send_json.assert_awaited_once_with(
                {"type": "new_message", "message_id": 123}
            )
        finally:
            websocket_manager.disconnect(device_id, websocket)

    asyncio.run(run())


def test_websocket_notify_unconnected_device():
    websocket_manager.notify_device(
        987656,
        {"type": "new_message", "message_id": 124},
    )
