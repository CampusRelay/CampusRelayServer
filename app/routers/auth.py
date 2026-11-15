from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import SESSION_COOKIE_NAME, get_current_session
from app.auth.security import (
    create_session_token,
    hash_session_token,
    verify_password,
)
from app.database import get_db
from app.models import PlatformAdmin, School, SchoolMembership, User, UserSession
from app.schemas.auth import LoginRequest, SelectSchoolRequest

router = APIRouter(prefix="/auth", tags=["Authentication"])

SESSION_HOURS = 12
SESSION_SECONDS = SESSION_HOURS * 60 * 60


def _active_schools(db: Session, user_id: int) -> list[dict]:
    rows = db.execute(
        select(SchoolMembership, School)
        .join(School, School.id == SchoolMembership.school_id)
        .where(
            SchoolMembership.user_id == user_id,
            SchoolMembership.status == "active",
            School.status == "active",
        )
        .order_by(School.id)
    ).all()

    return [
        {
            "school_id": school.id,
            "school_name": school.name,
            "role": membership.role,
        }
        for membership, school in rows
    ]


@router.post("/login")
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip().lower()
    user = db.scalar(select(User).where(User.username == username))

    if (
        user is None
        or user.status != "active"
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    is_platform_admin = db.scalar(
        select(PlatformAdmin.user_id).where(
            PlatformAdmin.user_id == user.id,
            PlatformAdmin.is_active.is_(True),
        )
    ) is not None

    schools = _active_schools(db, user.id)
    if not is_platform_admin and not schools:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active school membership",
        )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=SESSION_HOURS)
    token = create_session_token()

    db.add(
        UserSession(
            session_id=f"ses_{uuid4().hex}",
            user_id=user.id,
            token_hash=hash_session_token(token),
            created_at=now,
            expires_at=expires_at,
            active_school_id=schools[0]["school_id"] if schools else None,
        )
    )
    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )

    return {
        "status": "ok",
        "message": "Login successful",
        "user": {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
        },
        "expires_at": expires_at,
    }


@router.get("/me")
def get_me(
    current: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user = current["user"]
    session = current["session"]
    schools = _active_schools(db, user.id)

    active_school = next(
        (
            school for school in schools
            if school["school_id"] == session.active_school_id
        ),
        None,
    )

    return {
        "status": "ok",
        "user": {
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
        },
        "schools": schools,
        "active_school": active_school,
        "session": {"expires_at": session.expires_at},
    }


@router.post("/select-school")
def select_school(
    payload: SelectSchoolRequest,
    current: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user = current["user"]
    session = current["session"]

    row = db.execute(
        select(SchoolMembership, School)
        .join(School, School.id == SchoolMembership.school_id)
        .where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == payload.school_id,
            SchoolMembership.status == "active",
            School.status == "active",
        )
    ).first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active membership in this school",
        )

    membership, school = row
    session.active_school_id = school.id
    db.commit()

    return {
        "status": "ok",
        "active_school": {
            "school_id": school.id,
            "school_name": school.name,
            "role": membership.role,
        },
    }


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        session = db.scalar(
            select(UserSession).where(
                UserSession.token_hash == hash_session_token(token)
            )
        )
        if session is not None and session.revoked_at is None:
            session.revoked_at = datetime.now(timezone.utc)
            db.commit()

    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return {"status": "ok", "message": "Logged out"}
