from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_platform_admin
from app.database import get_db
from app.models import School, SchoolJoinCode
from app.services.registration import generate_join_code, hash_join_code

router = APIRouter(tags=["学校注册码"])


def get_school_for_update(db: Session, school_id: int):
    school = db.scalar(
        select(School).where(School.id == school_id).with_for_update()
    )
    if school is None:
        raise HTTPException(status_code=404, detail="学校不存在")
    return school


def issue_code(db: Session, school_id: int, admin_user_id: int):
    code = generate_join_code()
    record = SchoolJoinCode(
        school_id=school_id,
        code_hash=hash_join_code(code),
        is_active=True,
        created_by_user_id=admin_user_id,
    )
    db.add(record)
    db.flush()
    return code, record


@router.post("/platform/schools/{school_id}/join-code")
def create_school_join_code(
    school_id: int,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    get_school_for_update(db, school_id)
    active = db.scalar(
        select(SchoolJoinCode).where(
            SchoolJoinCode.school_id == school_id,
            SchoolJoinCode.is_active.is_(True),
        )
    )
    if active:
        raise HTTPException(status_code=409, detail="该学校已有有效注册码")

    try:
        code, record = issue_code(db, school_id, admin["user"].id)
        db.commit()
        db.refresh(record)
    except Exception:
        db.rollback()
        raise

    return {
        "school_id": school_id,
        "code": code,
        "is_active": True,
        "created_at": record.created_at,
    }


@router.get("/platform/schools/{school_id}/join-code")
def get_school_join_code(
    school_id: int,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    if db.get(School, school_id) is None:
        raise HTTPException(status_code=404, detail="学校不存在")

    record = db.scalar(
        select(SchoolJoinCode)
        .where(SchoolJoinCode.school_id == school_id)
        .order_by(SchoolJoinCode.created_at.desc(), SchoolJoinCode.id.desc())
        .limit(1)
    )
    if record is None:
        return {
            "school_id": school_id,
            "has_code": False,
            "is_active": False,
            "created_at": None,
            "revoked_at": None,
        }

    return {
        "school_id": school_id,
        "has_code": True,
        "is_active": record.is_active,
        "created_at": record.created_at,
        "revoked_at": record.revoked_at,
    }


@router.post("/platform/schools/{school_id}/join-code/rotate")
def rotate_school_join_code(
    school_id: int,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    get_school_for_update(db, school_id)
    now = datetime.now(timezone.utc)

    active_records = db.scalars(
        select(SchoolJoinCode)
        .where(
            SchoolJoinCode.school_id == school_id,
            SchoolJoinCode.is_active.is_(True),
        )
        .with_for_update()
    ).all()

    for record in active_records:
        record.is_active = False
        record.revoked_at = now

    try:
        code, new_record = issue_code(db, school_id, admin["user"].id)
        db.commit()
        db.refresh(new_record)
    except Exception:
        db.rollback()
        raise

    return {
        "school_id": school_id,
        "code": code,
        "is_active": True,
        "created_at": new_record.created_at,
    }


@router.post("/platform/schools/{school_id}/join-code/revoke")
def revoke_school_join_code(
    school_id: int,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    get_school_for_update(db, school_id)
    record = db.scalar(
        select(SchoolJoinCode)
        .where(
            SchoolJoinCode.school_id == school_id,
            SchoolJoinCode.is_active.is_(True),
        )
        .with_for_update()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="没有有效注册码")

    record.is_active = False
    record.revoked_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "school_id": school_id,
        "is_active": False,
        "revoked_at": record.revoked_at,
    }
