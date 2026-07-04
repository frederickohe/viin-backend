from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from another_fastapi_jwt_auth import AuthJWT
from sqlalchemy.orm import Session

from core.memory.dto.memory_dtos import (
    BriefingResponse,
    MemoryItemCreateRequest,
    MemoryItemResponse,
    MemoryListCreateRequest,
    MemoryListItemCreateRequest,
    MemoryListItemResponse,
    MemoryListResponse,
    MemorySearchResponse,
    ReminderCreateRequest,
    ReminderResponse,
)
from core.memory.model.memory_enums import MemoryItemType
from core.memory.service.briefing_service import BriefingPeriod, BriefingService
from core.memory.service.memory_service import MemoryService
from core.user.service.user_service import UserService
from utilities.dbconfig import SessionLocal

import jwt
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from fastapi import HTTPException


def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired. Please log in again.")
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


memory_routes = APIRouter()


@memory_routes.post("/items", response_model=MemoryItemResponse)
def create_memory_item(
    payload: MemoryItemCreateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    item = MemoryService(db).create_memory_item(
        owner_user_id=user.id,
        item_type=payload.item_type,
        title=payload.title,
        text=payload.text,
        url=payload.url,
        file_id=payload.file_id,
        tags=payload.tags,
        metadata=payload.metadata,
        visibility=payload.visibility,
    )
    return MemoryItemResponse.model_validate(item)


@memory_routes.get("/items", response_model=list[MemoryItemResponse])
def list_memory_items(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    rows = MemoryService(db).list_memory_items(owner_user_id=user.id, limit=limit)
    return [MemoryItemResponse.model_validate(r) for r in rows]


@memory_routes.delete("/items/{item_id}")
def delete_memory_item(
    item_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    MemoryService(db).delete_memory_item(owner_user_id=user.id, item_id=item_id)
    return {"message": "Deleted"}


@memory_routes.get("/search", response_model=MemorySearchResponse)
def search_memory(
    q: str = Query(..., min_length=1),
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
    limit: int = Query(10, ge=1, le=50),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    out = MemoryService(db).search_memory(owner_user_id=user.id, query=q, limit=limit)
    return MemorySearchResponse(
        hits=out["hits"],
        items=[MemoryItemResponse.model_validate(i) for i in out["items"]],
    )


@memory_routes.post("/lists", response_model=MemoryListResponse)
def create_list(
    payload: MemoryListCreateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    lst = MemoryService(db).create_list(owner_user_id=user.id, name=payload.name, description=payload.description)
    return MemoryListResponse.model_validate(lst)


@memory_routes.post("/lists/{list_id}/items", response_model=MemoryListItemResponse)
def add_list_item(
    list_id: str,
    payload: MemoryListItemCreateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    row = MemoryService(db).add_list_item(owner_user_id=user.id, list_id=list_id, text=payload.text)
    return MemoryListItemResponse.model_validate(row)


@memory_routes.post("/reminders", response_model=ReminderResponse)
def create_reminder(
    payload: ReminderCreateRequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    r = MemoryService(db).create_reminder(
        owner_user_id=user.id,
        title=payload.title,
        body=payload.body,
        due_at=payload.due_at,
        timezone_name=payload.timezone,
        rrule=payload.rrule,
        delivery=payload.delivery,
    )
    return ReminderResponse.model_validate(r)


@memory_routes.get("/briefing/daily", response_model=BriefingResponse)
def get_daily_briefing(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    svc = BriefingService(db)
    tasks = svc.collect_tasks(owner_user_id=user.id, period=BriefingPeriod.DAILY)
    body = svc.format_briefing(tasks=tasks, period=BriefingPeriod.DAILY)
    return BriefingResponse(period="daily", body=body, item_count=len(tasks))


@memory_routes.get("/briefing/weekly", response_model=BriefingResponse)
def get_weekly_briefing(
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    current_user_email = authjwt.get_jwt_subject()
    user = UserService(db).get_current_user(current_user_email)
    svc = BriefingService(db)
    tasks = svc.collect_tasks(owner_user_id=user.id, period=BriefingPeriod.WEEKLY)
    body = svc.format_briefing(tasks=tasks, period=BriefingPeriod.WEEKLY)
    return BriefingResponse(period="weekly", body=body, item_count=len(tasks))

