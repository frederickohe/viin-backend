from pydantic import BaseModel, Field


class ImageGenerationResponse(BaseModel):
    prompt: str
    image_base64: str = Field(..., description="Base64-encoded image bytes from Google (Nana Banana / Gemini image model)")
    mime_type: str = "image/png"


class VideoGenerationResponse(BaseModel):
    prompt: str
    video_url: str = Field(..., description="Direct URL from Google Veo (Generative Language API)")
    stored_url: str | None = Field(
        None,
        description="Contabo URL when store=true; omitted when returning Google URL only",
    )
