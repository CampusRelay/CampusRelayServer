from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import hash_session_token
from app.database import get_db
from app.models import PlatformAdmin, User, UserSession

SESSION_COOKIE_NAME = "campusrelay_session"


def get_current_session(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_session_token(token))
    ).first()

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session",
        )

    session, user = result

    if (
        session.revoked_at is not None
        or session.expires_at <= datetime.now(timezone.utc)
        or user.status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or revoked",
        )

    return {"session": session, "user": user}


def require_platform_admin(
    current: dict = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> dict:
    admin = db.scalar(
        select(PlatformAdmin).where(
            PlatformAdmin.user_id == current["user"].id,
            PlatformAdmin.is_active.is_(True),
        )
    )
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator permission required",
        )
    return current
