import pytest
from app.auth.security import hash_password
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models import (
    PlatformAdmin,
    School,
    SchoolJoinCode,
    User,
    SchoolMembership,
    TeacherRegistrationRequest,
)
from app.services.registration import hash_join_code


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def registration_school(db_session):
    school = School(
        name="教师注册测试学校",
        code="TEACHERREGTEST",
        status="active",
    )
    db_session.add(school)
    db_session.flush()

    db_session.add(
        SchoolJoinCode(
            school_id=school.id,
            code_hash=hash_join_code("ABC234"),
            is_active=True,
        )
    )
    db_session.flush()
    return school


def registration_payload(**overrides):
    data = {
        "school_name": "教师注册测试学校",
        "join_code": "ABC234",
        "username": "teacher_test_01",
        "display_name": "测试教师",
        "password": "TeacherPassword123!",
    }
    data.update(overrides)
    return data


def test_teacher_registration_with_valid_code(
    client, db_session, registration_school
):
    response = client.post(
        "/auth/register",
        json=registration_payload(),
    )
    assert response.status_code in (200, 201), response.text

    result = response.json()
    assert result.get("request_id")

    user = db_session.scalar(
        select(User).where(User.username == "teacher_test_01")
    )
    assert user is not None

    membership = db_session.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == registration_school.id,
        )
    )
    assert membership is not None


def test_teacher_registration_status_lookup(
    client, db_session, registration_school
):
    response = client.post(
        "/auth/register",
        json=registration_payload(username="teacher_status_01"),
    )
    assert response.status_code in (200, 201), response.text

    request_id = response.json().get("request_id")
    assert request_id

    status_response = client.get(
        f"/auth/registration-status/{request_id}"
    )
    assert status_response.status_code == 200, status_response.text

    result = status_response.json()
    assert result["request_id"] == request_id
    assert "password_hash" not in result


def test_teacher_registration_rejects_invalid_join_code(
    client, db_session, registration_school
):
    response = client.post(
        "/auth/register",
        json=registration_payload(
            username="teacher_bad_code",
            join_code="ZZZ999",
        ),
    )
    assert response.status_code in (400, 401, 403, 429)


def test_teacher_registration_rejects_unknown_school(
    client, db_session, registration_school
):
    response = client.post(
        "/auth/register",
        json=registration_payload(
            username="teacher_unknown_school",
            school_name="不存在的学校",
        ),
    )
    assert response.status_code in (400, 401, 404, 429)


def test_teacher_registration_rejects_short_password(
    client, db_session, registration_school
):
    response = client.post(
        "/auth/register",
        json=registration_payload(
            username="teacher_short_pw",
            password="123",
        ),
    )
    assert response.status_code == 422


def test_registration_status_unknown_request_returns_not_found(client):
    response = client.get(
        "/auth/registration-status/unknown-request-id"
    )
    assert response.status_code == 404


def seed_daily_quota(db_session, school):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    for index in range(10):
        db_session.add(
            TeacherRegistrationRequest(
                request_id=f"quota_seed_{index}",
                school_id=school.id,
                username=f"quota_seed_user_{index}",
                display_name="额度测试",
                password_hash=None,
                status="auto_approved",
                request_date=today,
            )
        )
    db_session.flush()


def create_and_login_platform_admin(client, db_session):
    user = User(
        username="registration_admin",
        password_hash=hash_password("AdminPassword123!"),
        display_name="平台管理员",
        status="active",
    )
    db_session.add(user)
    db_session.flush()

    db_session.add(
        PlatformAdmin(
            user_id=user.id,
            is_active=True,
        )
    )
    db_session.flush()

    response = client.post(
        "/auth/login",
        json={
            "username": "registration_admin",
            "password": "AdminPassword123!",
        },
    )
    assert response.status_code == 200, response.text


def create_pending_registration(
    client, db_session, school, username
):
    seed_daily_quota(db_session, school)

    response = client.post(
        "/auth/register",
        json=registration_payload(username=username),
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"

    row = db_session.scalar(
        select(TeacherRegistrationRequest).where(
            TeacherRegistrationRequest.request_id
            == response.json()["request_id"]
        )
    )
    assert row is not None
    assert row.password_hash
    return row


def test_teacher_registration_exceeds_daily_quota(
    client, db_session, registration_school
):
    seed_daily_quota(db_session, registration_school)

    response = client.post(
        "/auth/register",
        json=registration_payload(username="teacher_quota_11"),
    )

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"

    row = db_session.scalar(
        select(TeacherRegistrationRequest).where(
            TeacherRegistrationRequest.request_id
            == response.json()["request_id"]
        )
    )
    assert row is not None
    assert row.password_hash
    assert row.user_id is None

    user = db_session.scalar(
        select(User).where(User.username == "teacher_quota_11")
    )
    assert user is None


def test_admin_can_approve_pending_teacher_registration(
    client, db_session, registration_school
):
    row = create_pending_registration(
        client,
        db_session,
        registration_school,
        "teacher_pending_approve",
    )

    create_and_login_platform_admin(client, db_session)

    response = client.post(
        f"/platform/teacher-registrations/{row.request_id}/approve"
    )
    assert response.status_code == 200, response.text

    db_session.refresh(row)
    assert row.status == "approved"
    assert row.password_hash is None
    assert row.user_id is not None
    assert row.reviewed_at is not None
    assert row.reviewed_by_user_id is not None

    user = db_session.get(User, row.user_id)
    assert user is not None
    assert user.username == "teacher_pending_approve"

    membership = db_session.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == registration_school.id,
        )
    )
    assert membership is not None
    assert membership.role == "teacher"
    assert membership.status == "active"


def test_admin_can_reject_pending_teacher_registration(
    client, db_session, registration_school
):
    row = create_pending_registration(
        client,
        db_session,
        registration_school,
        "teacher_pending_reject",
    )

    create_and_login_platform_admin(client, db_session)

    response = client.post(
        f"/platform/teacher-registrations/{row.request_id}/reject",
        json={"reason": "信息需要核实"},
    )
    assert response.status_code == 200, response.text

    db_session.refresh(row)
    assert row.status == "rejected"
    assert row.password_hash is None
    assert row.reviewed_at is not None
    assert row.reviewed_by_user_id is not None
    assert row.review_note == "信息需要核实"

    user = db_session.scalar(
        select(User).where(User.username == "teacher_pending_reject")
    )
    assert user is None
