import logging

from fastapi import APIRouter, HTTPException, Query

from core.agent.dto.media_generation_request import MediaGenerationRequest
from core.agent.tools.google_image.google_image_service import (
    GoogleImageGenerationError,
    GoogleImageService,
)
from core.agent.tools.google_veo.google_veo_service import (
    GoogleVeoGenerationError,
    GoogleVeoService,
)
from core.media.dto.media_generation_response import (
    ImageGenerationResponse,
    VideoGenerationResponse,
)

logger = logging.getLogger(__name__)

media_routes = APIRouter()


@media_routes.post("/generate-image", response_model=ImageGenerationResponse)
async def generate_image(req: MediaGenerationRequest):
    """
    Generate an image via Google Generative Language API (Nana Banana / Gemini image model).
    Uses GOOGLE_API_KEY, NANA_BANANA_BASE_URL, and NANA_BANANA_MODEL from the environment.
    """
    try:
        service = GoogleImageService()
        b64 = await service.generate_image_base64(req.prompt, user_id=req.user_id)
        mime_type = service.last_mime_type or "image/png"
        return ImageGenerationResponse(prompt=req.prompt, image_base64=b64, mime_type=mime_type)
    except GoogleImageGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Image generation failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Image generation failed")


@media_routes.post("/generate-video", response_model=VideoGenerationResponse)
async def generate_video(
    req: MediaGenerationRequest,
    store: bool = Query(
        False,
        description="When true, download the Google video and upload to Contabo; stored_url is set.",
    ),
):
    """
    Generate a video via Google Veo (Generative Language API).
    By default returns the direct Google video URL. Set store=true to also persist on Contabo.
    """
    try:
        service = GoogleVeoService()
        if store:
            stored_url = await service.generate_video_and_store(req.prompt, user_id=req.user_id)
            return VideoGenerationResponse(
                prompt=req.prompt,
                video_url=stored_url,
                stored_url=stored_url,
            )
        video_url = await service.generate_video_url(req.prompt, user_id=req.user_id)
        return VideoGenerationResponse(
            prompt=req.prompt,
            video_url=video_url,
            stored_url=None,
        )
    except GoogleVeoGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Video generation failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Video generation failed")
