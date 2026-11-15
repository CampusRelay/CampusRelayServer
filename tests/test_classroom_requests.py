from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Classroom,
    ClassroomCreationRequest,
    ClassroomMembership,
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


def create_teacher(db, username="class_teacher", role="teacher"):
    user = User(
        username=username,
        password_hash=hash_password("TeacherPassword123!"),
        display_name="测试教师",
        status="active",
    )
    db.add(user)
    db.flush()

    school = School(
        name=f"班级申请测试学校_{username}",
        code=f"CR_{username}",
        status="active",
    )
    db.add(school)
    db.flush()

    db.add(
        SchoolMembership(
            user_id=user.id,
            school_id=school.id,
            role=role,
            status="active",
        )
    )
    db.flush()
    return user, school


def login_teacher(client, username="class_teacher"):
    response = client.post(
        "/auth/login",
        json={
            "username": username,
            "password": "TeacherPassword123!",
        },
    )
    assert response.status_code == 200, response.text


def create_admin(db):
    user = User(
        username="class_platform_admin",
        password_hash=hash_password("AdminPassword123!"),
        display_name="平台管理员",
        status="active",
    )
    db.add(user)
    db.flush()

    db.add(PlatformAdmin(user_id=user.id, is_active=True))
    db.flush()
    return user


def login_admin(client):
    response = client.post(
        "/auth/login",
        json={
            "username": "class_platform_admin",
            "password": "AdminPassword123!",
        },
    )
    assert response.status_code == 200, response.text


def seed_daily_quota(db, school, user):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()

    for index in range(10):
        db.add(
            ClassroomCreationRequest(
                request_id=f"class_quota_seed_{index}",
                school_id=school.id,
                user_id=user.id,
                class_name=f"额度占用班级{index}",
                status="auto_approved",
                request_date=today,
            )
        )
    db.flush()


def test_teacher_can_create_classroom_and_become_member(client, db_session):
    user, school = create_teacher(db_session)
    login_teacher(client)

    response = client.post(
        "/school/classrooms",
        json={"class_name": "七年级一班"},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "auto_approved"
    assert result["classroom_id"] is not None
    assert result["request_id"]

    classroom = db_session.get(Classroom, result["classroom_id"])
    assert classroom is not None
    assert classroom.name == "七年级一班"
    assert classroom.school_id == school.id

    membership = db_session.scalar(
        select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom.id,
            ClassroomMembership.user_id == user.id,
        )
    )
    assert membership is not None
    assert membership.role == "teacher"
    assert membership.status == "active"


def test_teacher_can_query_own_request_status(client, db_session):
    create_teacher(db_session)
    login_teacher(client)

    create_response = client.post(
        "/school/classrooms",
        json={"class_name": "状态查询班级"},
    )
    assert create_response.status_code == 200, create_response.text

    request_id = create_response.json()["request_id"]
    response = client.get(
        f"/school/classroom-creation-requests/{request_id}"
    )

    assert response.status_code == 200, response.text
    assert response.json()["request_id"] == request_id
    assert response.json()["status"] == "auto_approved"


def test_daily_quota_exceeded_creates_pending_request(client, db_session):
    user, school = create_teacher(db_session)
    seed_daily_quota(db_session, school, user)
    login_teacher(client)

    response = client.post(
        "/school/classrooms",
        json={"class_name": "等待审核班级"},
    )

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "pending"
    assert result["classroom_id"] is None

    classroom = db_session.scalar(
        select(Classroom).where(
            Classroom.school_id == school.id,
            Classroom.name == "等待审核班级",
        )
    )
    assert classroom is None


def test_duplicate_classroom_name_is_rejected(client, db_session):
    user, school = create_teacher(db_session)
    existing = Classroom(
        school_id=school.id,
        name="已存在班级",
        code="existing_class",
        status="active",
    )
    db_session.add(existing)
    db_session.flush()
    login_teacher(client)

    response = client.post(
        "/school/classrooms",
        json={"class_name": "已存在班级"},
    )

    assert response.status_code == 409


def test_duplicate_pending_request_is_rejected(client, db_session):
    user, school = create_teacher(db_session)
    seed_daily_quota(db_session, school, user)
    login_teacher(client)

    first = client.post(
        "/school/classrooms",
        json={"class_name": "重复申请班级"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "pending"

    second = client.post(
        "/school/classrooms",
        json={"class_name": "重复申请班级"},
    )
    assert second.status_code == 409


def test_platform_admin_can_list_and_approve_request(client, db_session):
    teacher, school = create_teacher(db_session)
    seed_daily_quota(db_session, school, teacher)
    login_teacher(client)

    create_response = client.post(
        "/school/classrooms",
        json={"class_name": "管理员审批班级"},
    )
    assert create_response.status_code == 200, create_response.text
    request_id = create_response.json()["request_id"]

    admin = create_admin(db_session)
    login_admin(client)

    list_response = client.get("/platform/classroom-creation-requests")
    assert list_response.status_code == 200, list_response.text
    assert any(
        item["request_id"] == request_id
        for item in list_response.json()["requests"]
    )

    approve_response = client.post(
        f"/platform/classroom-creation-requests/{request_id}/approve"
    )
    assert approve_response.status_code == 200, approve_response.text
    classroom_id = approve_response.json()["classroom_id"]

    request_row = db_session.scalar(
        select(ClassroomCreationRequest).where(
            ClassroomCreationRequest.request_id == request_id
        )
    )
    assert request_row.status == "approved"
    assert request_row.classroom_id == classroom_id
    assert request_row.reviewed_by_user_id == admin.id

    membership = db_session.scalar(
        select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom_id,
            ClassroomMembership.user_id == teacher.id,
        )
    )
    assert membership is not None
    assert membership.role == "teacher"


def test_platform_admin_can_reject_request(client, db_session):
    teacher, school = create_teacher(db_session)
    seed_daily_quota(db_session, school, teacher)
    login_teacher(client)

    create_response = client.post(
        "/school/classrooms",
        json={"class_name": "管理员拒绝班级"},
    )
    assert create_response.status_code == 200, create_response.text
    request_id = create_response.json()["request_id"]

    create_admin(db_session)
    login_admin(client)

    response = client.post(
        f"/platform/classroom-creation-requests/{request_id}/reject",
        json={"reason": "申请信息需要核实"},
    )
    assert response.status_code == 200, response.text

    row = db_session.scalar(
        select(ClassroomCreationRequest).where(
            ClassroomCreationRequest.request_id == request_id
        )
    )
    assert row.status == "rejected"
    assert row.review_note == "申请信息需要核实"
    assert row.reviewed_at is not None

    classroom = db_session.scalar(
        select(Classroom).where(
            Classroom.school_id == school.id,
            Classroom.name == "管理员拒绝班级",
        )
    )
    assert classroom is None


def test_normal_user_cannot_access_platform_request_list(client, db_session):
    create_teacher(db_session)
    login_teacher(client)

    response = client.get("/platform/classroom-creation-requests")
    assert response.status_code == 403


def test_student_cannot_create_classroom(client, db_session):
    create_teacher(db_session, role="student")
    login_teacher(client)

    response = client.post(
        "/school/classrooms",
        json={"class_name": "学生尝试创建班级"},
    )
    assert response.status_code == 403


def test_invalid_classroom_name_is_rejected(client, db_session):
    create_teacher(db_session)
    login_teacher(client)

    response = client.post(
        "/school/classrooms",
        json={"class_name": "   "},
    )
    assert response.status_code == 422
