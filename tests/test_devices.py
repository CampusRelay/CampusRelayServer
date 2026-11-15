import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Classroom,
    Device,
    DeviceBinding,
    PlatformAdmin,
    School,
    SchoolMembership,
    User,
)


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def school_data(db_session):
    user = User(
        username="device_admin",
        password_hash=hash_password("DevicePassword123!"),
        display_name="设备管理员",
        status="active",
    )
    db_session.add(user)
    db_session.flush()

    school = School(
        name="设备测试学校",
        code="DEVICE_TEST_SCHOOL",
        status="active",
    )
    db_session.add(school)
    db_session.flush()

    db_session.add(
        SchoolMembership(
            user_id=user.id,
            school_id=school.id,
            role="admin",
            status="active",
        )
    )

    classroom = Classroom(
        school_id=school.id,
        name="设备测试班级",
        code="DEVICE_CLASS_01",
        status="active",
    )
    db_session.add(classroom)
    db_session.flush()

    db_session.commit()
    return user, school, classroom


def login(client):
    response = client.post(
        "/auth/login",
        json={
            "username": "device_admin",
            "password": "DevicePassword123!",
        },
    )
    assert response.status_code == 200, response.text


def test_register_device_and_list(client, db_session, school_data):
    _, _, classroom = school_data
    login(client)

    response = client.post(
        "/school/devices",
        json={
            "serial_number": "DEVICE-001",
            "name": "教室显示屏",
            "device_type": "display",
            "classroom_id": classroom.id,
        },
    )

    assert response.status_code == 201, response.text
    device_id = response.json()["device_id"]

    device = db_session.get(Device, device_id)
    assert device is not None
    assert device.status == "active"

    binding = db_session.scalar(
        select(DeviceBinding).where(
            DeviceBinding.device_id == device_id,
            DeviceBinding.unbound_at.is_(None),
        )
    )
    assert binding is not None
    assert binding.classroom_id == classroom.id

    listing = client.get("/school/devices")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["devices"][0]["serial_number"] == "DEVICE-001"


def test_duplicate_serial_number_rejected(client, school_data):
    _, _, classroom = school_data
    login(client)

    payload = {
        "serial_number": "DEVICE-DUPLICATE",
        "name": "设备一",
        "classroom_id": classroom.id,
    }
    first = client.post("/school/devices", json=payload)
    assert first.status_code == 201, first.text

    second = client.post(
        "/school/devices",
        json={**payload, "name": "设备二"},
    )
    assert second.status_code == 409


def test_device_can_be_unbound_and_rebound(client, db_session, school_data):
    _, _, classroom = school_data
    login(client)

    created = client.post(
        "/school/devices",
        json={
            "serial_number": "DEVICE-REBIND",
            "name": "可重新绑定设备",
            "classroom_id": classroom.id,
        },
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["device_id"]

    unbound = client.post(f"/school/devices/{device_id}/unbind")
    assert unbound.status_code == 200, unbound.text

    db_session.expire_all()
    history = db_session.scalars(
        select(DeviceBinding).where(DeviceBinding.device_id == device_id)
    ).all()
    assert len(history) == 1
    assert history[0].unbound_at is not None

    rebound = client.post(
        f"/school/devices/{device_id}/bind",
        json={"classroom_id": classroom.id},
    )
    assert rebound.status_code == 200, rebound.text

    db_session.expire_all()
    history = db_session.scalars(
        select(DeviceBinding).where(DeviceBinding.device_id == device_id)
    ).all()
    assert len(history) == 2
    assert sum(item.unbound_at is None for item in history) == 1


def test_cannot_bind_device_twice(client, school_data):
    _, _, classroom = school_data
    login(client)

    created = client.post(
        "/school/devices",
        json={
            "serial_number": "DEVICE-DOUBLE",
            "name": "设备",
            "classroom_id": classroom.id,
        },
    )
    device_id = created.json()["device_id"]

    response = client.post(
        f"/school/devices/{device_id}/bind",
        json={"classroom_id": classroom.id},
    )
    assert response.status_code == 409


def test_cannot_bind_to_another_school_classroom(client, db_session, school_data):
    _, _, classroom = school_data

    other_school = School(
        name="其他学校",
        code="DEVICE_OTHER_SCHOOL",
        status="active",
    )
    db_session.add(other_school)
    db_session.flush()

    other_classroom = Classroom(
        school_id=other_school.id,
        name="其他学校班级",
        code="OTHER_CLASS",
        status="active",
    )
    db_session.add(other_classroom)
    db_session.commit()

    login(client)
    response = client.post(
        "/school/devices",
        json={
            "serial_number": "DEVICE-CROSS-SCHOOL",
            "name": "设备",
            "classroom_id": other_classroom.id,
        },
    )
    assert response.status_code == 404


def test_normal_teacher_cannot_manage_devices(client, db_session, school_data):
    user, school, _ = school_data
    membership = db_session.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == school.id,
        )
    )
    membership.role = "teacher"
    db_session.commit()

    login(client)
    response = client.get("/school/devices")
    assert response.status_code == 403


def test_device_registration_requires_authentication(client):
    response = client.get("/school/devices")
    assert response.status_code == 401
