from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_session
from app.database import get_db
from app.models import (
    Classroom, ClassroomMembership, SchoolMembership, User,
)

router = APIRouter(tags=["Classroom Members"])


class MemberCreate(BaseModel):
    user_id: int = Field(gt=0)
    role: str = Field(pattern="^(teacher|student|parent)$")


class MemberUpdate(BaseModel):
    role: str | None = Field(default=None, pattern="^(teacher|student|parent)$")
    status: str | None = Field(default=None, pattern="^(active|inactive)$")


def get_manager(current, db, classroom_id):
    school_id = current["session"].active_school_id
    if school_id is None:
        raise HTTPException(status_code=403, detail="Please select a school")

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

    return classroom, membership


def require_admin(membership):
    if membership.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Administrator permission required")


@router.get("/school/classrooms/{classroom_id}/members")
def list_members(
    classroom_id: int,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    get_manager(current, db, classroom_id)

    rows = db.execute(
        select(ClassroomMembership, User)
        .join(User, User.id == ClassroomMembership.user_id)
        .where(ClassroomMembership.classroom_id == classroom_id)
        .order_by(ClassroomMembership.joined_at.desc())
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "members": [
            {
                "user_id": member.user_id,
                "username": user.username,
                "display_name": user.display_name,
                "role": member.role,
                "status": member.status,
                "joined_at": member.joined_at,
            }
            for member, user in rows
        ],
    }


@router.post("/school/classrooms/{classroom_id}/members", status_code=201)
def add_member(
    classroom_id: int,
    data: MemberCreate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    classroom, manager = get_manager(current, db, classroom_id)
    require_admin(manager)

    school_member = db.scalar(select(SchoolMembership).where(
        SchoolMembership.user_id == data.user_id,
        SchoolMembership.school_id == classroom.school_id,
        SchoolMembership.status == "active",
    ))
    if school_member is None:
        raise HTTPException(status_code=404, detail="Active school member not found")

    if data.role == "teacher" and school_member.role not in ("teacher", "owner", "admin"):
        raise HTTPException(status_code=400, detail="User is not a school teacher")
    if data.role in ("student", "parent") and school_member.role != data.role:
        raise HTTPException(status_code=400, detail="School role does not match classroom role")

    user = db.get(User, data.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=404, detail="Active user not found")

    member = db.scalar(select(ClassroomMembership).where(
        ClassroomMembership.classroom_id == classroom_id,
        ClassroomMembership.user_id == data.user_id,
    ))

    if member is not None:
        if member.status == "active":
            raise HTTPException(status_code=409, detail="User is already a member")
        member.role = data.role
        member.status = "active"
        member.joined_at = datetime.now(timezone.utc)
    else:
        member = ClassroomMembership(
            classroom_id=classroom_id,
            user_id=data.user_id,
            role=data.role,
            status="active",
        )
        db.add(member)

    db.commit()
    db.refresh(member)

    return {
        "status": "ok",
        "user_id": member.user_id,
        "role": member.role,
        "membership_status": member.status,
    }


@router.patch("/school/classrooms/{classroom_id}/members/{user_id}")
def update_member(
    classroom_id: int,
    user_id: int,
    data: MemberUpdate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, manager = get_manager(current, db, classroom_id)
    require_admin(manager)

    member = db.scalar(select(ClassroomMembership).where(
        ClassroomMembership.classroom_id == classroom_id,
        ClassroomMembership.user_id == user_id,
    ))
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    if data.role is None and data.status is None:
        raise HTTPException(status_code=400, detail="No changes supplied")

    if data.role is not None:
        member.role = data.role
    if data.status is not None:
        member.status = data.status

    db.commit()
    return {
        "status": "ok",
        "user_id": user_id,
        "role": member.role,
        "membership_status": member.status,
    }


@router.delete("/school/classrooms/{classroom_id}/members/{user_id}")
def remove_member(
    classroom_id: int,
    user_id: int,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, manager = get_manager(current, db, classroom_id)
    require_admin(manager)

    member = db.scalar(select(ClassroomMembership).where(
        ClassroomMembership.classroom_id == classroom_id,
        ClassroomMembership.user_id == user_id,
    ))
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")

    member.status = "inactive"
    db.commit()

    return {"status": "ok", "user_id": user_id, "membership_status": "inactive"}
