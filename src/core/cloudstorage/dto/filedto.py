from datetime import datetime
from pydantic import BaseModel, Field
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


class FileUploadRagResponse(FileDTO):
    """Response for authenticated upload + optional Qdrant indexing."""

    rag_indexed_chunks: int = 0
    rag_detail: Optional[str] = None
