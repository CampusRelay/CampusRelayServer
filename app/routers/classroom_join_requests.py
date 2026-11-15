import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_session
from app.database import get_db
from app.models import (
    Classroom,
    ClassroomJoinRequest,
    ClassroomMembership,
    SchoolMembership,
    User,
)

router = APIRouter(tags=["Classroom Join Requests"])


class JoinRequestCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    requested_role: str = Field(pattern="^(student|parent)$")


class JoinRequestReview(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    review_note: str | None = Field(default=None, max_length=200)


def get_active_school(current):
    school_id = current["session"].active_school_id
    if school_id is None:
        raise HTTPException(status_code=403, detail="Please select a school")
    return school_id


def get_classroom_manager(current, db, classroom_id):
    school_id = get_active_school(current)

    classroom = db.scalar(select(Classroom).where(
        Classroom.id == classroom_id,
        Classroom.school_id == school_id,
        Classroom.status == "active",
    ))
    if classroom is None:
        raise HTTPException(status_code=404, detail="Classroom not found")

    membership = db.scalar(select(SchoolMembership).where(
        SchoolMembership.user_id == current["user"].id,
        SchoolMembership.school_id == school_id,
        SchoolMembership.status == "active",
        SchoolMembership.role.in_(("owner", "admin", "teacher")),
    ))
    if membership is None:
        raise HTTPException(status_code=403, detail="Permission denied")

    if membership.role == "teacher":
        teaching = db.scalar(select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom_id,
            ClassroomMembership.user_id == current["user"].id,
            ClassroomMembership.role == "teacher",
            ClassroomMembership.status == "active",
        ))
        if teaching is None:
            raise HTTPException(status_code=403, detail="Not your classroom")

    return classroom


@router.post("/classroom/join-requests", status_code=201)
def create_join_request(
    data: JoinRequestCreate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    school_id = get_active_school(current)
    user = current["user"]

    classroom = db.scalar(select(Classroom).where(
        Classroom.code == data.code.strip(),
        Classroom.school_id == school_id,
        Classroom.status == "active",
    ))
    if classroom is None:
        raise HTTPException(status_code=404, detail="Invalid classroom code")

    school_member = db.scalar(select(SchoolMembership).where(
        SchoolMembership.user_id == user.id,
        SchoolMembership.school_id == school_id,
        SchoolMembership.status == "active",
        SchoolMembership.role == data.requested_role,
    ))
    if school_member is None:
        raise HTTPException(
            status_code=403,
            detail="Active school membership with matching role required",
        )

    existing_member = db.scalar(select(ClassroomMembership).where(
        ClassroomMembership.classroom_id == classroom.id,
        ClassroomMembership.user_id == user.id,
        ClassroomMembership.status == "active",
    ))
    if existing_member is not None:
        raise HTTPException(status_code=409, detail="Already a classroom member")

    pending = db.scalar(select(ClassroomJoinRequest).where(
        ClassroomJoinRequest.classroom_id == classroom.id,
        ClassroomJoinRequest.user_id == user.id,
        ClassroomJoinRequest.status == "pending",
    ))
    if pending is not None:
        raise HTTPException(status_code=409, detail="A pending request already exists")

    request = ClassroomJoinRequest(
        request_id=secrets.token_urlsafe(24),
        classroom_id=classroom.id,
        user_id=user.id,
        requested_role=data.requested_role,
        status="pending",
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    return {
        "status": "ok",
        "request_id": request.request_id,
        "classroom_id": classroom.id,
        "classroom_name": classroom.name,
        "requested_role": request.requested_role,
        "request_status": request.status,
        "created_at": request.created_at,
    }


@router.get("/classroom/join-requests/mine")
def list_my_join_requests(
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    school_id = get_active_school(current)

    rows = db.execute(
        select(ClassroomJoinRequest, Classroom)
        .join(Classroom, Classroom.id == ClassroomJoinRequest.classroom_id)
        .where(
            ClassroomJoinRequest.user_id == current["user"].id,
            Classroom.school_id == school_id,
        )
        .order_by(ClassroomJoinRequest.created_at.desc())
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "requests": [
            {
                "request_id": request.request_id,
                "classroom_id": classroom.id,
                "classroom_name": classroom.name,
                "requested_role": request.requested_role,
                "request_status": request.status,
                "created_at": request.created_at,
                "reviewed_at": request.reviewed_at,
                "review_note": request.review_note,
            }
            for request, classroom in rows
        ],
    }


@router.get("/school/classrooms/{classroom_id}/join-requests")
def list_classroom_join_requests(
    classroom_id: int,
    status_filter: str = "pending",
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    get_classroom_manager(current, db, classroom_id)

    if status_filter not in ("pending", "approved", "rejected", "all"):
        raise HTTPException(status_code=400, detail="Invalid status filter")

    query = (
        select(ClassroomJoinRequest, User)
        .join(User, User.id == ClassroomJoinRequest.user_id)
        .where(ClassroomJoinRequest.classroom_id == classroom_id)
        .order_by(ClassroomJoinRequest.created_at.desc())
    )
    if status_filter != "all":
        query = query.where(ClassroomJoinRequest.status == status_filter)

    rows = db.execute(query).all()

    return {
        "status": "ok",
        "total": len(rows),
        "requests": [
            {
                "request_id": request.request_id,
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name,
                "requested_role": request.requested_role,
                "request_status": request.status,
                "created_at": request.created_at,
                "reviewed_at": request.reviewed_at,
                "review_note": request.review_note,
            }
            for request, user in rows
        ],
    }


@router.post("/school/classrooms/{classroom_id}/join-requests/{request_id}/review")
def review_join_request(
    classroom_id: int,
    request_id: str,
    data: JoinRequestReview,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    classroom = get_classroom_manager(current, db, classroom_id)

    request = db.scalar(select(ClassroomJoinRequest).where(
        ClassroomJoinRequest.request_id == request_id,
        ClassroomJoinRequest.classroom_id == classroom_id,
    ))
    if request is None:
        raise HTTPException(status_code=404, detail="Join request not found")

    if request.status != "pending":
        raise HTTPException(status_code=409, detail="Request already reviewed")

    now = datetime.now(timezone.utc)

    if data.decision == "approved":
        school_member = db.scalar(select(SchoolMembership).where(
            SchoolMembership.user_id == request.user_id,
            SchoolMembership.school_id == classroom.school_id,
            SchoolMembership.status == "active",
            SchoolMembership.role == request.requested_role,
        ))
        user = db.get(User, request.user_id)

        if school_member is None or user is None or user.status != "active":
            raise HTTPException(
                status_code=409,
                detail="Applicant no longer has an active matching school membership",
            )

        member = db.scalar(select(ClassroomMembership).where(
            ClassroomMembership.classroom_id == classroom_id,
            ClassroomMembership.user_id == request.user_id,
        ))

        if member is None:
            member = ClassroomMembership(
                classroom_id=classroom_id,
                user_id=request.user_id,
                role=request.requested_role,
                status="active",
            )
            db.add(member)
        else:
            member.role = request.requested_role
            member.status = "active"
            member.joined_at = now

    request.status = data.decision
    request.reviewed_at = now
    request.reviewed_by_user_id = current["user"].id
    request.review_note = data.review_note

    db.commit()

    return {
        "status": "ok",
        "request_id": request.request_id,
        "request_status": request.status,
        "reviewed_at": request.reviewed_at,
    }
