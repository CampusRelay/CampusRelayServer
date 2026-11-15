from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.security import hash_password
from app.database import get_db
from app.main import app
from app.models import (
    Classroom,
    ClassroomJoinRequest,
    ClassroomMembership,
    School,
    SchoolMembership,
    User,
)
import pytest


@pytest.fixture
def client(db_session):
    app.dependency_overrides.clear()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client

    app.dependency_overrides.clear()


def create_join_test_data(db):
    school = School(
        name="Join Request Test School",
        code="JOIN_REQUEST_TEST_SCHOOL",
        status="active",
    )
    db.add(school)
    db.flush()

    teacher = User(
        username="join_test_teacher",
        password_hash=hash_password("TeacherPassword123!"),
        display_name="Test Teacher",
        status="active",
    )
    student = User(
        username="join_test_student",
        password_hash=hash_password("StudentPassword123!"),
        display_name="Test Student",
        status="active",
    )
    db.add_all([teacher, student])
    db.flush()

    db.add_all([
        SchoolMembership(
            user_id=teacher.id,
            school_id=school.id,
            role="teacher",
            status="active",
        ),
        SchoolMembership(
            user_id=student.id,
            school_id=school.id,
            role="student",
            status="active",
        ),
    ])

    classroom = Classroom(
        school_id=school.id,
        name="Join Request Test Classroom",
        code="join_request_test_code",
        status="active",
    )
    db.add(classroom)
    db.flush()

    db.add(ClassroomMembership(
        classroom_id=classroom.id,
        user_id=teacher.id,
        role="teacher",
        status="active",
    ))
    db.flush()

    return teacher, student, classroom


def login(client, username, password):
    response = client.post(
        "/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def test_student_can_submit_and_view_join_request(client, db_session):
    _, student, classroom = create_join_test_data(db_session)
    login(client, "join_test_student", "StudentPassword123!")

    response = client.post(
        "/classroom/join-requests",
        json={
            "code": classroom.code,
            "requested_role": "student",
        },
    )
    assert response.status_code == 201, response.text
    request_id = response.json()["request_id"]
    assert response.json()["request_status"] == "pending"

    mine = client.get("/classroom/join-requests/mine")
    assert mine.status_code == 200, mine.text
    assert mine.json()["total"] == 1
    assert mine.json()["requests"][0]["request_id"] == request_id


def test_duplicate_pending_join_request_is_rejected(client, db_session):
    _, _, classroom = create_join_test_data(db_session)
    login(client, "join_test_student", "StudentPassword123!")

    payload = {
        "code": classroom.code,
        "requested_role": "student",
    }
    first = client.post("/classroom/join-requests", json=payload)
    assert first.status_code == 201, first.text

    second = client.post("/classroom/join-requests", json=payload)
    assert second.status_code == 409


def test_teacher_can_review_and_approve_join_request(client, db_session):
    teacher, student, classroom = create_join_test_data(db_session)

    login(client, "join_test_student", "StudentPassword123!")
    submitted = client.post(
        "/classroom/join-requests",
        json={
            "code": classroom.code,
            "requested_role": "student",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["request_id"]

    client.post("/auth/logout")
    login(client, "join_test_teacher", "TeacherPassword123!")

    listed = client.get(
        f"/school/classrooms/{classroom.id}/join-requests"
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1

    reviewed = client.post(
        f"/school/classrooms/{classroom.id}/join-requests/{request_id}/review",
        json={
            "decision": "approved",
            "review_note": "Approved",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["request_status"] == "approved"

    membership = db_session.scalar(
        select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom.id,
            ClassroomMembership.user_id == student.id,
        )
    )
    assert membership is not None
    assert membership.role == "student"
    assert membership.status == "active"

    request = db_session.scalar(
        select(ClassroomJoinRequest).where(
            ClassroomJoinRequest.request_id == request_id
        )
    )
    assert request.status == "approved"
    assert request.reviewed_by_user_id == teacher.id


def test_teacher_can_reject_join_request(client, db_session):
    _, student, classroom = create_join_test_data(db_session)

    login(client, "join_test_student", "StudentPassword123!")
    submitted = client.post(
        "/classroom/join-requests",
        json={
            "code": classroom.code,
            "requested_role": "student",
        },
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["request_id"]

    client.post("/auth/logout")
    login(client, "join_test_teacher", "TeacherPassword123!")

    reviewed = client.post(
        f"/school/classrooms/{classroom.id}/join-requests/{request_id}/review",
        json={
            "decision": "rejected",
            "review_note": "Not approved",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["request_status"] == "rejected"

    membership = db_session.scalar(
        select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom.id,
            ClassroomMembership.user_id == student.id,
        )
    )
    assert membership is None
