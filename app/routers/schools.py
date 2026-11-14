
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import School
from app.schemas.schools import (
    SchoolCreate,
    SchoolResponse,
    SchoolUpdate,
)

router = APIRouter(prefix="/api/schools", tags=["Schools"])


def normalize_school_data(data: dict) -> dict:
    """清理学校文本字段。"""
    for field in ("name", "code", "address", "contact", "phone"):
        if field in data:
            if data[field] is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} 不能为 null",
                )
            data[field] = data[field].strip()

    if "name" in data and not data["name"]:
        raise HTTPException(status_code=422, detail="学校名称不能为空")

    if "code" in data and not data["code"]:
        raise HTTPException(status_code=422, detail="学校代码不能为空")

    return data


def ensure_unique_code(
    db: Session,
    code: str,
    exclude_id: int | None = None,
) -> None:
    statement = select(School.id).where(
        func.lower(School.code) == code.lower()
    )

    if exclude_id is not None:
        statement = statement.where(School.id != exclude_id)

    if db.scalar(statement) is not None:
        raise HTTPException(
            status_code=409,
            detail="该学校代码已经存在",
        )


@router.get("", response_model=list[SchoolResponse])
def list_schools(db: Session = Depends(get_db)):
    statement = select(School).order_by(School.id.desc())
    return db.scalars(statement).all()


@router.get("/{school_id}", response_model=SchoolResponse)
def get_school(school_id: int, db: Session = Depends(get_db)):
    school = db.get(School, school_id)

    if school is None:
        raise HTTPException(status_code=404, detail="学校不存在")

    return school


@router.post(
    "",
    response_model=SchoolResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_school(
    payload: SchoolCreate,
    db: Session = Depends(get_db),
):
    data = normalize_school_data(payload.model_dump())

    ensure_unique_code(db, data["code"])

    school = School(**data)
    db.add(school)

    try:
        db.commit()
        db.refresh(school)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="学校代码已存在或数据冲突",
        )

    return school


@router.patch("/{school_id}", response_model=SchoolResponse)
def update_school(
    school_id: int,
    payload: SchoolUpdate,
    db: Session = Depends(get_db),
):
    school = db.get(School, school_id)

    if school is None:
        raise HTTPException(status_code=404, detail="学校不存在")

    data = normalize_school_data(
        payload.model_dump(exclude_unset=True)
    )

    if "code" in data:
        ensure_unique_code(db, data["code"], exclude_id=school_id)

    for field, value in data.items():
        setattr(school, field, value)

    try:
        db.commit()
        db.refresh(school)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="学校信息更新失败，可能存在重复代码",
        )

    return school


@router.delete("/{school_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_school(
    school_id: int,
    db: Session = Depends(get_db),
):
    school = db.get(School, school_id)

    if school is None:
        raise HTTPException(status_code=404, detail="学校不存在")

    db.delete(school)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="该学校仍有关联班级，不能删除",
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
