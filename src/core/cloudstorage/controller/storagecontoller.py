import io

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    UploadFile,
    status,
    Query,
    File,
)
from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel
from core.auth.service.sessiondriver import SessionDriver, TokenData
from another_fastapi_jwt_auth import AuthJWT
from core.cloudstorage.dto.filedto import (
    FileDTO,
    FileUploadRagResponse,
    RagIndexFromUrlRequest,
    RagIndexJobStartedResponse,
    RagIndexJobStatusResponse,
)
from core.exceptions import *
from core.notification.dto.request.notificationcreate import NotificationCreateRequest
from core.notification.dto.request.notificationupdate import NotificationUpdateRequest
from utilities.dbconfig import SessionLocal
from sqlalchemy.orm import Session
import logging
from core.notification.model.Notification import Notification, NotificationStatus, NotificationType
from core.user.model.User import User

# DTO Models
from core.notification.dto.response.notification_response import NotificationResponse
from core.notification.dto.response.paged_notifications import PagedNotificationResponse
from core.notification.dto.response.message_response import MessageResponse

from core.notification.service.notification_service import NotificationService
from another_fastapi_jwt_auth.exceptions import MissingTokenError
from core.cloudstorage.service.storageservice import StorageService
from core.cloudstorage.service.storageservice import StorageFolder
from core.subscription.service.subscription_service import SubscriptionService
from core.user.service.user_service import UserService
from core.rag.rag_index_job_store import RagIndexJobStore
from core.rag.rag_index_service import RagIndexService

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Reuse your existing token validation and DB dependencies
from core.user.controller.usercontroller import validate_token, get_db

from fastapi.responses import FileResponse, JSONResponse
import os


storage_routes = APIRouter()

storage_service = StorageService()
rag_job_store = RagIndexJobStore()
rag_index_service = RagIndexService(
    storage_service=storage_service,
    job_store=rag_job_store,
)


def _safe_user_prefix(subject: str) -> str:
    # S3 keys can include many characters, but we keep it conservative and stable.
    safe = (subject or "unknown").strip()
    safe = safe.replace("\\", "_").replace("/", "_")
    return f"{safe}/"


def _require_subscribed_user(db: Session, subject: str):
    user_service = UserService(db)
    user = user_service.get_current_user(subject)
    sub = SubscriptionService(db).get_user_active_subscription(str(user.id))
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription is required to index documents for RAG.",
        )
    return user


def _user_data_from_user(user) -> dict:
    return {
        "db_user_id": user.id,
        "company": user.company,
        "user_id": user.phone,
    }


def _job_status_response(record) -> RagIndexJobStatusResponse:
    result = None
    if record.result:
        result = FileUploadRagResponse.model_validate(record.result)
    return RagIndexJobStatusResponse(
        job_id=record.job_id,
        status=record.status.value if hasattr(record.status, "value") else record.status,
        progress=record.progress,
        message=record.message,
        source_type=record.source_type,
        source_label=record.source_label,
        result=result,
        error=record.error,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


@storage_routes.post("/upload", response_model=FileDTO)
async def upload_file(
    file: UploadFile,
    folder: Optional[StorageFolder] = Query(None),
    authjwt: AuthJWT = Depends(validate_token),
):
    safe_name = os.path.basename(file.filename)
    url = storage_service.upload_file(
        file.file,
        safe_name,
        content_type=file.content_type,
        folder=folder,
    )
    return FileDTO(
        file_name=safe_name,
        file_url=url,
        folder=folder.value if folder else None,
        uploaded_at=datetime.now(timezone.utc),
    )


@storage_routes.post("/me/upload-rag-document")
async def upload_rag_document_for_subscribed_user(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    folder: StorageFolder = Query(default=StorageFolder.chatbot_files),
    async_mode: bool = Query(
        default=False,
        description="When true, returns immediately with a job_id; poll GET /me/rag-index-jobs/{job_id} for progress.",
    ),
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Upload a document to object storage and index extractable text into Qdrant for the
    authenticated user (tenant id `user:{internal_user_id}` by default).

    Requires an active subscription. Supported extraction matches FileContentExtractor
    (txt, pdf, docx, csv, xlsx).

    Use `async_mode=true` for progress monitoring via the rag-index-jobs endpoint.
    """
    subject = authjwt.get_jwt_subject()
    user = _require_subscribed_user(db, subject)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")

    safe_name = os.path.basename(file.filename or "upload")
    user_data = _user_data_from_user(user)

    if async_mode:
        job = rag_job_store.create_job(
            user_subject=subject,
            source_type="file",
            source_label=safe_name,
        )
        background_tasks.add_task(
            rag_index_service.run_file_job,
            job_id=job.job_id,
            subject=subject,
            user_data=user_data,
            raw=raw,
            safe_name=safe_name,
            content_type=file.content_type,
            folder=folder,
        )
        payload = RagIndexJobStartedResponse(
            job_id=job.job_id,
            status=job.status.value,
            progress=job.progress,
            message=job.message,
            poll_url=f"/api/v1/storage/me/rag-index-jobs/{job.job_id}",
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=payload.model_dump(mode="json"),
        )

    return rag_index_service.index_file_sync(
        subject=subject,
        user_data=user_data,
        raw=raw,
        safe_name=safe_name,
        content_type=file.content_type,
        folder=folder,
        upload_file=file,
    )


@storage_routes.post("/me/upload-rag-url", response_model=RagIndexJobStartedResponse)
async def upload_rag_url_for_subscribed_user(
    body: RagIndexFromUrlRequest,
    background_tasks: BackgroundTasks,
    folder: StorageFolder = Query(default=StorageFolder.chatbot_files),
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Scrape a public website, store the extracted text, and index it into Qdrant.

    Returns immediately with a job_id. Poll GET /me/rag-index-jobs/{job_id} for progress.
    """
    subject = authjwt.get_jwt_subject()
    user = _require_subscribed_user(db, subject)
    url = str(body.url)

    job = rag_job_store.create_job(
        user_subject=subject,
        source_type="url",
        source_label=url,
    )
    background_tasks.add_task(
        rag_index_service.run_url_job,
        job_id=job.job_id,
        subject=subject,
        user_data=_user_data_from_user(user),
        url=url,
        folder=folder,
    )
    return RagIndexJobStartedResponse(
        job_id=job.job_id,
        status=job.status.value,
        progress=job.progress,
        message=job.message,
        poll_url=f"/api/v1/storage/me/rag-index-jobs/{job.job_id}",
    )


@storage_routes.get(
    "/me/rag-index-jobs/{job_id}",
    response_model=RagIndexJobStatusResponse,
)
async def get_rag_index_job_status(
    job_id: str,
    authjwt: AuthJWT = Depends(validate_token),
):
    """Poll indexing progress for an async file or URL upload."""
    subject = authjwt.get_jwt_subject()
    record = rag_job_store.get_job(job_id)
    if not record or record.user_subject != subject:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return _job_status_response(record)


@storage_routes.post("/me/upload-multiple", response_model=List[FileDTO])
async def upload_multiple_files_for_me(
    files: List[UploadFile] = File(...),
    folder: StorageFolder = Query(...),
    authjwt: AuthJWT = Depends(validate_token),
):
    subject = authjwt.get_jwt_subject()
    user_prefix = _safe_user_prefix(subject)

    uploaded: List[FileDTO] = []
    for f in files:
        safe_name = os.path.basename(f.filename)
        key_name = f"{user_prefix}{safe_name}"
        url = storage_service.upload_file(
            f.file,
            key_name,
            content_type=f.content_type,
            folder=folder,
        )
        object_key = f"{storage_service.resolve_subfolder(folder=folder)}{key_name}"
        uploaded.append(
            FileDTO(
                file_name=safe_name,
                file_url=url,
                folder=folder.value,
                object_key=object_key,
                uploaded_at=datetime.now(timezone.utc),
            )
        )

    return uploaded


@storage_routes.get("/me/files", response_model=List[FileDTO])
async def list_my_files_in_folder(
    folder: StorageFolder = Query(...),
    authjwt: AuthJWT = Depends(validate_token),
):
    subject = authjwt.get_jwt_subject()
    user_prefix = _safe_user_prefix(subject)

    objects = storage_service.list_files(folder=folder, prefix=user_prefix)
    items: list[FileDTO] = []
    for o in objects:
        meta = o.get("metadata") or {}
        source_type = meta.get("source-type")
        source_url = meta.get("source-url")
        items.append(
            FileDTO(
                file_name=os.path.basename(o["key"]),
                file_url=o["url"],
                folder=folder.value,
                object_key=o["key"],
                uploaded_at=o.get("last_modified"),
                source_type=source_type,
                source_url=source_url,
            )
        )
    return items


@storage_routes.get("/me/download/{file_name}")
async def download_my_file(
    file_name: str,
    folder: StorageFolder = Query(...),
    authjwt: AuthJWT = Depends(validate_token),
):
    subject = authjwt.get_jwt_subject()
    user_prefix = _safe_user_prefix(subject)

    safe_name = os.path.basename(file_name)
    key_name = f"{user_prefix}{safe_name}"

    os.makedirs("./downloads", exist_ok=True)
    destination_path = f"./downloads/{safe_name}"
    try:
        storage_service.download_file(key_name, destination_path, folder=folder)
    except Exception:
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

    return FileResponse(destination_path, filename=safe_name)


@storage_routes.delete("/me/file/{file_name}", response_model=MessageResponse)
async def delete_my_file(
    file_name: str,
    folder: StorageFolder = Query(...),
    authjwt: AuthJWT = Depends(validate_token),
):
    subject = authjwt.get_jwt_subject()
    user_prefix = _safe_user_prefix(subject)

    safe_name = os.path.basename(file_name)
    key_name = f"{user_prefix}{safe_name}"

    try:
        storage_service.delete_file(key_name, folder=folder)
    except Exception:
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")

    return MessageResponse(message="File deleted successfully")

@storage_routes.get("/download/{file_name}")
async def download_file(
    file_name: str,
    folder: Optional[StorageFolder] = Query(None),
    authjwt: AuthJWT = Depends(validate_token),
):
    # Sanitize file name
    safe_name = os.path.basename(file_name)

    # Ensure downloads directory exists
    os.makedirs("./downloads", exist_ok=True)

    destination_path = f"./downloads/{safe_name}"
    try:
        storage_service.download_file(safe_name, destination_path, folder=folder)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File not found: {safe_name}")
    

    # Return file to client
    return FileResponse(destination_path, filename=safe_name)
