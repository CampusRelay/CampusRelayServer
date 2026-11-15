import secrets

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import require_platform_admin
from app.auth.security import hash_password
from app.database import get_db
from app.models import (
    RegistrationRateLimit,
    School,
    SchoolJoinCode,
    SchoolMembership,
    TeacherRegistrationRequest,
    User,
)
from app.schemas.teacher_registration import (
    TeacherRegistrationCreate,
    TeacherRegistrationReject,
)
from app.services.registration import (
    hash_registration_ip,
    verify_join_code,
)

router = APIRouter(tags=["Teacher Registration"])

BEIJING_TZ = ZoneInfo("Asia/Shanghai")

MAX_DAILY_AUTO_REGISTRATIONS = 10
RATE_WINDOW = timedelta(minutes=15)
RATE_LOCK_DURATION = timedelta(minutes=15)
MAX_FAILED_ATTEMPTS = 5


def utc_now():
    return datetime.now(timezone.utc)


def registration_date():
    return datetime.now(BEIJING_TZ).date().isoformat()


def get_client_ip(request):
    if request.client is None:
        return "unknown"
    return request.client.host


def check_rate_limit(db: Session, ip_hash: str, now):
    row = db.scalar(
        select(RegistrationRateLimit)
        .where(RegistrationRateLimit.ip_hash == ip_hash)
        .with_for_update()
    )

    if row and row.locked_until and row.locked_until > now:
        retry_seconds = max(
            1,
            int((row.locked_until - now).total_seconds()),
        )
        raise HTTPException(
            status_code=429,
            detail="注册尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_seconds)},
        )


def record_failed_attempt(db: Session, ip_hash: str, now):
    row = db.scalar(
        select(RegistrationRateLimit)
        .where(RegistrationRateLimit.ip_hash == ip_hash)
        .with_for_update()
    )

    if row is None:
        row = RegistrationRateLimit(
            ip_hash=ip_hash,
            window_started_at=now,
            failed_attempts=1,
            updated_at=now,
        )
        db.add(row)
    else:
        if now - row.window_started_at >= RATE_WINDOW:
            row.window_started_at = now
            row.failed_attempts = 1
        else:
            row.failed_attempts += 1

        row.updated_at = now

    if row.failed_attempts >= MAX_FAILED_ATTEMPTS:
        row.locked_until = now + RATE_LOCK_DURATION


@router.post("/auth/register")
def register_teacher(
    data: TeacherRegistrationCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
):
    now = utc_now()
    ip_hash = hash_registration_ip(get_client_ip(request))

    try:
        check_rate_limit(db, ip_hash, now)

        school = db.scalar(
            select(School).where(
                func.lower(School.name) == data.school_name.lower(),
                School.status == "active",
            )
        )

        join_code_valid = False

        if school is not None:
            code_record = db.scalar(
                select(SchoolJoinCode).where(
                    SchoolJoinCode.school_id == school.id,
                    SchoolJoinCode.is_active.is_(True),
                )
            )

            if code_record:
                join_code_valid = verify_join_code(
                    data.join_code,
                    code_record.code_hash,
                )

        if not join_code_valid:
            record_failed_attempt(db, ip_hash, now)
            db.commit()
            raise HTTPException(
                status_code=401,
                detail="学校名称或注册码不正确",
            )

        db.query(RegistrationRateLimit).filter(
            RegistrationRateLimit.ip_hash == ip_hash
        ).delete(synchronize_session=False)

        existing_user = db.scalar(
            select(User).where(
                func.lower(User.username) == data.username
            )
        )

        if existing_user:
            raise HTTPException(
                status_code=409,
                detail="该登录账号已经被使用",
            )

        pending_request = db.scalar(
            select(TeacherRegistrationRequest).where(
                func.lower(TeacherRegistrationRequest.username) == data.username,
                TeacherRegistrationRequest.status == "pending",
            )
        )

        if pending_request:
            raise HTTPException(
                status_code=409,
                detail="该账号已有待审核申请",
            )

        # 同一学校的注册申请串行计算每日额度，防止并发突破上限。
        school = db.scalar(
            select(School)
            .where(School.id == school.id)
            .with_for_update()
        )

        if school is None or school.status != "active":
            raise HTTPException(
                status_code=401,
                detail="学校名称或注册码不正确",
            )

        today = registration_date()

        auto_count = db.scalar(
            select(func.count())
            .select_from(TeacherRegistrationRequest)
            .where(
                TeacherRegistrationRequest.school_id == school.id,
                TeacherRegistrationRequest.request_date == today,
                TeacherRegistrationRequest.status == "auto_approved",
            )
        ) or 0

        password_hash = hash_password(data.password)
        request_id = "trq_" + secrets.token_urlsafe(32)

        auto_approved = auto_count < MAX_DAILY_AUTO_REGISTRATIONS
        user_id = None

        if auto_approved:
            user = User(
                username=data.username,
                password_hash=password_hash,
                display_name=data.display_name,
                status="active",
            )
            db.add(user)
            db.flush()

            user_id = user.id

            db.add(
                SchoolMembership(
                    user_id=user.id,
                    school_id=school.id,
                    role="teacher",
                    status="active",
                )
            )

        registration = TeacherRegistrationRequest(
            request_id=request_id,
            school_id=school.id,
            username=data.username,
            display_name=data.display_name,
            password_hash=None if auto_approved else password_hash,
            status="auto_approved" if auto_approved else "pending",
            request_date=today,
            user_id=user_id,
        )

        db.add(registration)
        db.commit()

        response.status_code = 201 if auto_approved else 202

        return {
            "status": registration.status,
            "request_id": request_id,
            "message": (
                "注册成功，账号已经激活"
                if auto_approved
                else "今日自动注册名额已用完，申请等待平台管理员审核"
            ),
            "user_id": user_id,
            "school_name": school.name,
        }

    except HTTPException:
        if db.in_transaction():
            db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="注册信息冲突，请检查账号是否已经使用",
        )


@router.get("/auth/registration-status/{request_id}")
def get_registration_status(
    request_id: str,
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(TeacherRegistrationRequest).where(
            TeacherRegistrationRequest.request_id == request_id
        )
    )

    if row is None:
        raise HTTPException(status_code=404, detail="注册申请不存在")

    return {
        "status": row.status,
        "request_id": row.request_id,
        "username": row.username,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
    }


@router.get("/platform/teacher-registrations")
def list_teacher_registrations(
    status: str = Query(default="pending"),
    limit: int = Query(default=100, ge=1, le=200),
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    allowed = (
        "pending",
        "auto_approved",
        "approved",
        "rejected",
        "all",
    )

    if status not in allowed:
        raise HTTPException(status_code=422, detail="无效的申请状态")

    query = (
        select(TeacherRegistrationRequest, School.name)
        .join(
            School,
            School.id == TeacherRegistrationRequest.school_id,
        )
    )

    if status != "all":
        query = query.where(
            TeacherRegistrationRequest.status == status
        )

    rows = db.execute(
        query.order_by(
            TeacherRegistrationRequest.created_at.desc()
        ).limit(limit)
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "registrations": [
            {
                "request_id": row.request_id,
                "school_id": row.school_id,
                "school_name": school_name,
                "username": row.username,
                "display_name": row.display_name,
                "status": row.status,
                "request_date": row.request_date,
                "created_at": row.created_at,
                "reviewed_at": row.reviewed_at,
                "review_note": row.review_note,
                "user_id": row.user_id,
            }
            for row, school_name in rows
        ],
    }


@router.post("/platform/teacher-registrations/{request_id}/approve")
def approve_teacher_registration(
    request_id: str,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    try:
        row = db.scalar(
            select(TeacherRegistrationRequest)
            .where(
                TeacherRegistrationRequest.request_id == request_id
            )
            .with_for_update()
        )

        if row is None:
            raise HTTPException(status_code=404, detail="注册申请不存在")

        if row.status != "pending":
            raise HTTPException(status_code=409, detail="该申请已经处理")

        if not row.password_hash:
            raise HTTPException(status_code=409, detail="申请缺少有效密码哈希")

        school = db.get(School, row.school_id)

        if school is None or school.status != "active":
            raise HTTPException(
                status_code=409,
                detail="所属学校不存在或已停用",
            )

        duplicate = db.scalar(
            select(User).where(
                func.lower(User.username) == row.username
            )
        )

        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="该账号已经被其他用户使用",
            )

        now = utc_now()

        user = User(
            username=row.username,
            password_hash=row.password_hash,
            display_name=row.display_name,
            status="active",
        )
        db.add(user)
        db.flush()

        db.add(
            SchoolMembership(
                user_id=user.id,
                school_id=row.school_id,
                role="teacher",
                status="active",
            )
        )

        row.status = "approved"
        row.password_hash = None
        row.reviewed_at = now
        row.reviewed_by_user_id = admin["user"].id
        row.user_id = user.id
        row.review_note = "平台管理员批准"

        db.commit()

        return {
            "status": "ok",
            "message": "教师注册申请已批准",
            "user_id": user.id,
            "username": user.username,
        }

    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="创建教师账号时发生数据冲突",
        )


@router.post("/platform/teacher-registrations/{request_id}/reject")
def reject_teacher_registration(
    request_id: str,
    data: TeacherRegistrationReject,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(TeacherRegistrationRequest)
        .where(
            TeacherRegistrationRequest.request_id == request_id
        )
        .with_for_update()
    )

    if row is None:
        raise HTTPException(status_code=404, detail="注册申请不存在")

    if row.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已经处理")

    row.status = "rejected"
    row.password_hash = None
    row.reviewed_at = utc_now()
    row.reviewed_by_user_id = admin["user"].id
    row.review_note = data.reason.strip() or "管理员拒绝注册"

    db.commit()

    return {
        "status": "ok",
        "message": "注册申请已拒绝",
    }
