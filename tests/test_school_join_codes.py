import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import PlatformAdmin, School, SchoolJoinCode, User


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


def create_admin(db):
    user = User(
        username="joincode_admin",
        password_hash=hash_password("TestPassword123!"),
        display_name="测试管理员",
        status="active",
    )
    db.add(user)
    db.flush()

    db.add(PlatformAdmin(user_id=user.id, is_active=True))

    school = School(
        name="注册码测试学校",
        code="JOINCODETEST",
        status="active",
    )
    db.add(school)
    db.flush()

    return user, school


def login(client):
    response = client.post(
        "/auth/login",
        json={
            "username": "joincode_admin",
            "password": "TestPassword123!",
        },
    )
    assert response.status_code == 200, response.text


def test_create_join_code_and_query_status(client, db_session):
    _, school = create_admin(db_session)
    login(client)

    response = client.post(
        f"/platform/schools/{school.id}/join-code"
    )
    assert response.status_code == 200, response.text

    result = response.json()
    assert len(result["code"]) == 6
    assert result["is_active"] is True

    record = db_session.scalar(
        select(SchoolJoinCode).where(
            SchoolJoinCode.school_id == school.id
        )
    )
    assert record is not None
    assert record.code_hash != result["code"]
    assert result["code"] not in record.code_hash

    status = client.get(
        f"/platform/schools/{school.id}/join-code"
    )
    assert status.status_code == 200
    assert status.json()["has_code"] is True
    assert status.json()["is_active"] is True
    assert "code" not in status.json()
    assert "code_hash" not in status.json()


def test_create_duplicate_join_code_returns_conflict(client, db_session):
    _, school = create_admin(db_session)
    login(client)

    first = client.post(
        f"/platform/schools/{school.id}/join-code"
    )
    assert first.status_code == 200

    second = client.post(
        f"/platform/schools/{school.id}/join-code"
    )
    assert second.status_code == 409


def test_rotate_join_code_invalidates_old_code(client, db_session):
    _, school = create_admin(db_session)
    login(client)

    first = client.post(
        f"/platform/schools/{school.id}/join-code"
    )
    assert first.status_code == 200
    old_code = first.json()["code"]

    rotated = client.post(
        f"/platform/schools/{school.id}/join-code/rotate"
    )
    assert rotated.status_code == 200, rotated.text

    new_code = rotated.json()["code"]
    assert len(new_code) == 6
    assert new_code != old_code

    records = db_session.scalars(
        select(SchoolJoinCode).where(
            SchoolJoinCode.school_id == school.id
        )
    ).all()

    assert len(records) == 2
    assert sum(record.is_active for record in records) == 1
    assert any(
        not record.is_active and record.revoked_at is not None
        for record in records
    )


def test_revoke_join_code(client, db_session):
    _, school = create_admin(db_session)
    login(client)

    created = client.post(
        f"/platform/schools/{school.id}/join-code"
    )
    assert created.status_code == 200

    revoked = client.post(
        f"/platform/schools/{school.id}/join-code/revoke"
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["is_active"] is False

    status = client.get(
        f"/platform/schools/{school.id}/join-code"
    )
    assert status.status_code == 200
    assert status.json()["has_code"] is True
    assert status.json()["is_active"] is False


def test_revoke_without_active_code_returns_not_found(client, db_session):
    _, school = create_admin(db_session)
    login(client)

    response = client.post(
        f"/platform/schools/{school.id}/join-code/revoke"
    )
    assert response.status_code == 404


def test_join_code_endpoints_require_authentication(client, db_session):
    _, school = create_admin(db_session)

    response = client.get(
        f"/platform/schools/{school.id}/join-code"
    )
    assert response.status_code == 401


def test_join_code_unknown_school_returns_not_found(client, db_session):
    create_admin(db_session)
    login(client)

    response = client.get(
        "/platform/schools/999999/join-code"
    )
    assert response.status_code == 404
