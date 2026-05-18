from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional


class FileDTO(BaseModel):
    file_name: str
    file_url: str
    folder: Optional[str] = None
    object_key: Optional[str] = None
    uploaded_at: Optional[datetime] = Field(
        default=None,
        description="Last modified time from object storage (typically upload time).",
    )
    source_type: Optional[str] = Field(
        default=None,
        description="RAG source kind, e.g. document or website.",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="Original URL when source_type is website.",
    )


class FileUploadRagResponse(FileDTO):
    """Response for authenticated upload + optional Qdrant indexing."""

    rag_indexed_chunks: int = 0
    rag_detail: Optional[str] = None


class RagIndexFromUrlRequest(BaseModel):
    url: HttpUrl
    folder: Optional[str] = None


class RagIndexJobStartedResponse(BaseModel):
    job_id: str
    status: str = "pending"
    progress: int = 0
    message: str = "Queued"
    poll_url: str


class RagIndexJobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int = Field(ge=0, le=100)
    message: str
    source_type: str
    source_label: Optional[str] = None
    result: Optional[FileUploadRagResponse] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
