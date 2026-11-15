from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Classroom,
    ClassroomMembership,
    Device,
    DeviceBinding,
    Message,
    MessageDelivery,
    School,
    SchoolMembership,
    User,
)


def test_message_delivery_workflow(db_session):
    app.dependency_overrides.clear()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app, base_url="https://testserver")

    try:
        admin = User(
            username="message_test_admin",
            password_hash=hash_password("MessagePassword123!"),
            display_name="消息测试管理员",
            status="active",
        )
        db_session.add(admin)
        db_session.flush()

        school = School(
            name="消息测试学校",
            code="MESSAGE_TEST_SCHOOL",
            status="active",
        )
        db_session.add(school)
        db_session.flush()

        db_session.add(SchoolMembership(
            user_id=admin.id,
            school_id=school.id,
            role="admin",
            status="active",
        ))

        classroom = Classroom(
            school_id=school.id,
            name="消息测试班级",
            code="MESSAGE_CLASS_01",
            status="active",
        )
        db_session.add(classroom)
        db_session.flush()

        device = Device(
            serial_number="MESSAGE-DEVICE-001",
            name="消息测试显示屏",
            device_type="display",
            status="active",
        )
        db_session.add(device)
        db_session.flush()

        db_session.add(DeviceBinding(
            device_id=device.id,
            classroom_id=classroom.id,
            bound_by_user_id=admin.id,
        ))
        db_session.commit()

        login = client.post(
            "/auth/login",
            json={
                "username": "message_test_admin",
                "password": "MessagePassword123!",
            },
        )
        assert login.status_code == 200, login.text

        sent = client.post(
            f"/school/classrooms/{classroom.id}/messages",
            json={
                "title": "测试通知",
                "content": "这是一条测试消息",
                "message_type": "text",
            },
        )
        assert sent.status_code == 201, sent.text

        message_id = sent.json()["message_id"]
        assert sent.json()["delivery_count"] == 1

        message = db_session.get(Message, message_id)
        assert message is not None
        assert message.content == "这是一条测试消息"

        delivery = db_session.scalar(
            select(MessageDelivery).where(
                MessageDelivery.message_id == message_id,
                MessageDelivery.device_id == device.id,
            )
        )
        assert delivery is not None
        assert delivery.status == "pending"

        pull_url = (
            f"/devices/{device.id}/messages"
            "?serial_number=MESSAGE-DEVICE-001"
        )
        pulled = client.get(pull_url)
        assert pulled.status_code == 200, pulled.text
        assert pulled.json()["total"] == 1
        assert pulled.json()["messages"][0]["message_id"] == message_id

        db_session.refresh(delivery)
        assert delivery.status == "delivered"
        assert delivery.attempts == 1
        assert delivery.delivered_at is not None

        # 未确认的消息允许重新拉取，作为设备重试机制
        repeated = client.get(pull_url)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["total"] == 1

        db_session.refresh(delivery)
        assert delivery.attempts == 2

        ack = client.post(
            f"/devices/{device.id}/messages/{message_id}/ack"
            "?serial_number=MESSAGE-DEVICE-001"
        )
        assert ack.status_code == 200, ack.text
        assert ack.json()["delivery_status"] == "acknowledged"

        db_session.refresh(delivery)
        assert delivery.acknowledged_at is not None

        # 已确认消息不再出现在待处理列表
        after_ack = client.get(pull_url)
        assert after_ack.status_code == 200
        assert after_ack.json()["total"] == 0

        listing = client.get(
            f"/school/classrooms/{classroom.id}/messages"
        )
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert listing.json()["messages"][0]["deliveries"]["acknowledged"] == 1

    finally:
        app.dependency_overrides.clear()


def test_message_only_targets_currently_bound_devices(db_session):
    app.dependency_overrides.clear()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, base_url="https://testserver")

    try:
        admin = User(
            username="message_binding_admin",
            password_hash=hash_password("MessagePassword123!"),
            display_name="绑定测试管理员",
            status="active",
        )
        db_session.add(admin)
        db_session.flush()

        school = School(
            name="绑定测试学校",
            code="MESSAGE_BINDING_SCHOOL",
            status="active",
        )
        db_session.add(school)
        db_session.flush()

        db_session.add(SchoolMembership(
            user_id=admin.id,
            school_id=school.id,
            role="admin",
            status="active",
        ))

        classroom = Classroom(
            school_id=school.id,
            name="绑定测试班级",
            code="MESSAGE_BINDING_CLASS",
            status="active",
        )
        db_session.add(classroom)
        db_session.flush()

        device = Device(
            serial_number="MESSAGE-UNBOUND-001",
            name="已解绑设备",
            device_type="display",
            status="active",
        )
        db_session.add(device)
        db_session.flush()

        db_session.add(DeviceBinding(
            device_id=device.id,
            classroom_id=classroom.id,
            bound_by_user_id=admin.id,
            unbound_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        ))
        db_session.commit()

        login = client.post(
            "/auth/login",
            json={
                "username": "message_binding_admin",
                "password": "MessagePassword123!",
            },
        )
        assert login.status_code == 200, login.text

        sent = client.post(
            f"/school/classrooms/{classroom.id}/messages",
            json={"content": "不应投递给已解绑设备"},
        )
        assert sent.status_code == 201, sent.text
        assert sent.json()["delivery_count"] == 0

        deliveries = db_session.scalars(
            select(MessageDelivery).where(
                MessageDelivery.message_id == sent.json()["message_id"]
            )
        ).all()
        assert deliveries == []

    finally:
        app.dependency_overrides.clear()
