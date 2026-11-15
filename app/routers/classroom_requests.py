import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_session, require_platform_admin
from app.database import get_db
from app.models import (
    Classroom,
    ClassroomCreationRequest,
    ClassroomMembership,
    School,
    SchoolMembership,
    User,
)
from app.schemas.classroom_requests import (
    ClassroomCreationRequestCreate,
    ClassroomCreationRequestReject,
    ClassroomUpdate,
)

router = APIRouter(tags=["Classroom Requests"])

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
MAX_DAILY_AUTO_CREATIONS = 10


def utc_now():
    return datetime.now(timezone.utc)


def beijing_date():
    return datetime.now(BEIJING_TZ).date().isoformat()


def get_school_context(current, db):
    session = current["session"]
    user = current["user"]
    school_id = session.active_school_id

    if school_id is None:
        raise HTTPException(status_code=403, detail="请先选择学校")

    membership = db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == school_id,
            SchoolMembership.status == "active",
        )
    )
    if membership is None or membership.role not in ("owner", "admin", "teacher"):
        raise HTTPException(status_code=403, detail="无权在当前学校创建班级")

    school = db.get(School, school_id)
    if school is None or school.status != "active":
        raise HTTPException(status_code=403, detail="当前学校不可用")

    return user, school


def create_classroom(db, school_id, class_name, teacher_user_id):
    classroom = Classroom(
        school_id=school_id,
        name=class_name,
        code="cls_" + secrets.token_urlsafe(12),
        status="active",
    )
    db.add(classroom)
    db.flush()

    db.add(
        ClassroomMembership(
            classroom_id=classroom.id,
            user_id=teacher_user_id,
            role="teacher",
            status="active",
        )
    )
    db.flush()
    return classroom


@router.post("/school/classrooms")
def create_classroom_request(
    data: ClassroomCreationRequestCreate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    try:
        user, school = get_school_context(current, db)

        # 锁定学校行，使同一学校的每日额度计算串行执行。
        school = db.scalar(
            select(School).where(School.id == school.id).with_for_update()
        )

        duplicate_class = db.scalar(
            select(Classroom).where(
                Classroom.school_id == school.id,
                func.lower(Classroom.name) == data.class_name.lower(),
            )
        )
        if duplicate_class:
            raise HTTPException(status_code=409, detail="该学校已存在同名班级")

        duplicate_request = db.scalar(
            select(ClassroomCreationRequest).where(
                ClassroomCreationRequest.school_id == school.id,
                func.lower(ClassroomCreationRequest.class_name) == data.class_name.lower(),
                ClassroomCreationRequest.status == "pending",
            )
        )
        if duplicate_request:
            raise HTTPException(status_code=409, detail="该班级名称已有待审核申请")

        today = beijing_date()
        auto_count = db.scalar(
            select(func.count()).select_from(ClassroomCreationRequest).where(
                ClassroomCreationRequest.school_id == school.id,
                ClassroomCreationRequest.request_date == today,
                ClassroomCreationRequest.status == "auto_approved",
            )
        ) or 0

        auto_approved = auto_count < MAX_DAILY_AUTO_CREATIONS
        classroom = None
        if auto_approved:
            classroom = create_classroom(db, school.id, data.class_name, user.id)

        request_id = "ccr_" + secrets.token_urlsafe(24)
        request_row = ClassroomCreationRequest(
            request_id=request_id,
            school_id=school.id,
            user_id=user.id,
            class_name=data.class_name,
            status="auto_approved" if auto_approved else "pending",
            request_date=today,
            classroom_id=classroom.id if classroom else None,
        )
        db.add(request_row)
        db.commit()

        return {
            "status": request_row.status,
            "request_id": request_id,
            "classroom_id": classroom.id if classroom else None,
            "message": "班级创建成功" if auto_approved else "今日自动创建额度已用完，申请等待平台审核",
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="班级名称或申请信息发生冲突")


@router.get("/school/classroom-creation-requests/{request_id}")
def get_classroom_creation_status(
    request_id: str,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school = get_school_context(current, db)
    row = db.scalar(
        select(ClassroomCreationRequest).where(
            ClassroomCreationRequest.request_id == request_id,
            ClassroomCreationRequest.school_id == school.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="班级创建申请不存在")

    return {
        "request_id": row.request_id,
        "class_name": row.class_name,
        "status": row.status,
        "classroom_id": row.classroom_id,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
        "review_note": row.review_note,
    }


@router.get("/platform/classroom-creation-requests")
def list_classroom_creation_requests(
    status: str = Query(default="pending"),
    limit: int = Query(default=100, ge=1, le=200),
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    allowed = ("pending", "auto_approved", "approved", "rejected", "all")
    if status not in allowed:
        raise HTTPException(status_code=422, detail="无效的申请状态")

    query = (
        select(ClassroomCreationRequest, School.name, User.display_name)
        .join(School, School.id == ClassroomCreationRequest.school_id)
        .join(User, User.id == ClassroomCreationRequest.user_id)
    )
    if status != "all":
        query = query.where(ClassroomCreationRequest.status == status)

    rows = db.execute(
        query.order_by(ClassroomCreationRequest.created_at.desc()).limit(limit)
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "requests": [
            {
                "request_id": row.request_id,
                "school_id": row.school_id,
                "school_name": school_name,
                "user_id": row.user_id,
                "display_name": display_name,
                "class_name": row.class_name,
                "status": row.status,
                "request_date": row.request_date,
                "created_at": row.created_at,
                "reviewed_at": row.reviewed_at,
                "review_note": row.review_note,
                "classroom_id": row.classroom_id,
            }
            for row, school_name, display_name in rows
        ],
    }


@router.post("/platform/classroom-creation-requests/{request_id}/approve")
def approve_classroom_creation_request(
    request_id: str,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    try:
        row = db.scalar(
            select(ClassroomCreationRequest)
            .where(ClassroomCreationRequest.request_id == request_id)
            .with_for_update()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="班级创建申请不存在")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail="该申请已经处理")

        school = db.scalar(select(School).where(School.id == row.school_id).with_for_update())
        if school is None or school.status != "active":
            raise HTTPException(status_code=409, detail="所属学校不存在或已停用")

        duplicate = db.scalar(
            select(Classroom).where(
                Classroom.school_id == row.school_id,
                func.lower(Classroom.name) == row.class_name.lower(),
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="该学校已存在同名班级")

        classroom = create_classroom(db, row.school_id, row.class_name, row.user_id)
        row.status = "approved"
        row.classroom_id = classroom.id
        row.reviewed_at = utc_now()
        row.reviewed_by_user_id = admin["user"].id
        row.review_note = "平台管理员批准"
        db.commit()

        return {"status": "ok", "classroom_id": classroom.id, "message": "班级创建申请已批准"}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="创建班级时发生数据冲突")


@router.post("/platform/classroom-creation-requests/{request_id}/reject")
def reject_classroom_creation_request(
    request_id: str,
    data: ClassroomCreationRequestReject,
    admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    row = db.scalar(
        select(ClassroomCreationRequest)
        .where(ClassroomCreationRequest.request_id == request_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="班级创建申请不存在")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="该申请已经处理")

    row.status = "rejected"
    row.reviewed_at = utc_now()
    row.reviewed_by_user_id = admin["user"].id
    row.review_note = data.reason.strip() or "管理员拒绝班级创建申请"
    db.commit()

    return {"status": "ok", "message": "班级创建申请已拒绝"}


def get_school_admin_context(current, db):
    user, school = get_school_context(current, db)

    membership = db.scalar(
        select(SchoolMembership).where(
            SchoolMembership.user_id == user.id,
            SchoolMembership.school_id == school.id,
            SchoolMembership.status == "active",
            SchoolMembership.role.in_(("owner", "admin")),
        )
    )
    if membership is None:
        raise HTTPException(status_code=403, detail="需要学校管理员权限")

    return user, school


@router.get("/school/classrooms")
def list_school_classrooms(
    include_inactive: bool = False,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school = get_school_context(current, db)

    query = select(Classroom).where(Classroom.school_id == school.id)
    if not include_inactive:
        query = query.where(Classroom.status == "active")

    rows = db.scalars(
        query.order_by(Classroom.created_at.desc(), Classroom.id.desc())
    ).all()

    return {
        "status": "ok",
        "total": len(rows),
        "classrooms": [
            {
                "id": row.id,
                "name": row.name,
                "code": row.code,
                "grade": row.grade,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in rows
        ],
    }


@router.patch("/school/classrooms/{classroom_id}")
def update_school_classroom(
    classroom_id: int,
    data: ClassroomUpdate,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school = get_school_admin_context(current, db)

    classroom = db.scalar(
        select(Classroom)
        .where(
            Classroom.id == classroom_id,
            Classroom.school_id == school.id,
            Classroom.status == "active",
        )
        .with_for_update()
    )
    if classroom is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    changes = data.model_fields_set
    if not changes:
        raise HTTPException(status_code=422, detail="没有提供需要修改的内容")

    if "name" in changes and data.name is not None:
        duplicate = db.scalar(
            select(Classroom).where(
                Classroom.school_id == school.id,
                Classroom.id != classroom.id,
                Classroom.status == "active",
                func.lower(Classroom.name) == data.name.lower(),
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="班级名称已存在")
        classroom.name = data.name

    if "grade" in changes:
        classroom.grade = data.grade

    db.commit()
    db.refresh(classroom)

    return {
        "status": "ok",
        "classroom": {
            "id": classroom.id,
            "name": classroom.name,
            "code": classroom.code,
            "grade": classroom.grade,
            "status": classroom.status,
        },
    }


@router.delete("/school/classrooms/{classroom_id}")
def deactivate_school_classroom(
    classroom_id: int,
    current=Depends(get_current_session),
    db: Session = Depends(get_db),
):
    _, school = get_school_admin_context(current, db)

    classroom = db.scalar(
        select(Classroom)
        .where(
            Classroom.id == classroom_id,
            Classroom.school_id == school.id,
            Classroom.status == "active",
        )
        .with_for_update()
    )
    if classroom is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    classroom.status = "inactive"
    db.commit()

    return {"status": "ok", "message": "班级已停用"}
