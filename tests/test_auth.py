import pytest
from fastapi.testclient import TestClient

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import PlatformAdmin, School, SchoolMembership, User


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()


def create_user(
    db,
    username="test_user",
    password="TestPassword123!",
    with_school=True,
    platform_admin=False,
):
    user = User(
        username=username,
        password_hash=hash_password(password),
        display_name="Test User",
        status="active",
    )
    db.add(user)
    db.flush()

    if with_school:
        school = School(
            name="Test School",
            code=f"SCHOOL_{username}",
            status="active",
        )
        db.add(school)
        db.flush()

        db.add(
            SchoolMembership(
                user_id=user.id,
                school_id=school.id,
                role="teacher",
                status="active",
            )
        )

    if platform_admin:
        db.add(
            PlatformAdmin(
                user_id=user.id,
                is_active=True,
            )
        )

    db.flush()
    return user


def login(client, username="test_user", password="TestPassword123!"):
    return client.post(
        "/auth/login",
        json={
            "username": username,
            "password": password,
        },
    )


def test_login_success(client, db_session):
    create_user(db_session)

    response = login(client)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["user"]["username"] == "test_user"
    assert "campusrelay_session" in client.cookies


def test_login_invalid_password(client, db_session):
    create_user(db_session)

    response = login(client, password="WrongPassword123!")

    assert response.status_code == 401


def test_login_without_active_school_membership(client, db_session):
    create_user(db_session, with_school=False)

    response = login(client)

    assert response.status_code == 403


def test_get_current_user(client, db_session):
    create_user(db_session)

    login_response = login(client)
    assert login_response.status_code == 200

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["username"] == "test_user"
    assert len(response.json()["schools"]) == 1


def test_logout_revokes_session(client, db_session):
    create_user(db_session)

    login_response = login(client)
    assert login_response.status_code == 200

    logout_response = client.post("/auth/logout")
    assert logout_response.status_code == 200

    me_response = client.get("/auth/me")
    assert me_response.status_code == 401


def test_school_api_requires_authentication(client):
    response = client.get("/api/schools")

    assert response.status_code == 401


def test_school_api_forbids_normal_user(client, db_session):
    create_user(db_session)

    login_response = login(client)
    assert login_response.status_code == 200

    response = client.get("/api/schools")

    assert response.status_code == 403


def test_school_api_allows_platform_admin(client, db_session):
    create_user(
        db_session,
        username="platform_admin",
        with_school=False,
        platform_admin=True,
    )

    login_response = login(
        client,
        username="platform_admin",
    )
    assert login_response.status_code == 200

    response = client.get("/api/schools")

    assert response.status_code == 200
    assert isinstance(response.json(), list)
