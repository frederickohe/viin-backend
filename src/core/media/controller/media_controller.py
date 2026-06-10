import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from core.agent.dto.media_generation_request import MediaGenerationRequest
from core.credits.model.credit_types import CreditType
from core.credits.service.credit_service import CreditService
from core.user.controller.usercontroller import get_db, validate_token
from another_fastapi_jwt_auth import AuthJWT
from core.agent.tools.google_image.google_image_service import (
    GoogleImageGenerationError,
    GoogleImageService,
    GoogleImageTimeoutError,
)
from core.agent.tools.google_veo.google_veo_service import (
    GoogleVeoGenerationError,
    GoogleVeoService,
    GoogleVeoTimeoutError,
)
from core.media.dto.media_generation_response import (
    ImageGenerationResponse,
    VideoGenerationResponse,
)

logger = logging.getLogger(__name__)

media_routes = APIRouter()


def _deduct_media_credit(
    db: Session,
    credit_type: str,
    operation: str,
    authjwt: AuthJWT | None,
    req_user_id: str | None,
) -> None:
    user_id = None
    if authjwt:
        user_id = authjwt.get_jwt_subject()
    if not user_id and req_user_id:
        user_id = CreditService(db).resolve_user_id(req_user_id)
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required for media generation.")
    CreditService(db).require_credits(user_id, credit_type, 1.0, operation)


@media_routes.post("/generate-image", response_model=ImageGenerationResponse)
async def generate_image(
    req: MediaGenerationRequest,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """
    Generate an image via Google Generative Language API (Nana Banana / Gemini image model).
    Uses GOOGLE_API_KEY, NANA_BANANA_BASE_URL, and NANA_BANANA_MODEL from the environment.
    """
    _deduct_media_credit(db, CreditType.IMAGE_GEN.value, "image_generation", authjwt, req.user_id)
    try:
        service = GoogleImageService()
        b64 = await service.generate_image_base64(req.prompt, user_id=req.user_id)
        mime_type = service.last_mime_type or "image/png"
        return ImageGenerationResponse(prompt=req.prompt, image_base64=b64, mime_type=mime_type)
    except GoogleImageTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
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
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """
    Generate a video via Google Veo (Generative Language API).
    By default returns the direct Google video URL. Set store=true to also persist on Contabo.
    """
    _deduct_media_credit(db, CreditType.VIDEO_GEN.value, "video_generation", authjwt, req.user_id)
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
    except GoogleVeoTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except GoogleVeoGenerationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Video generation failed: %s", str(e), exc_info=True)
        raise HTTPException(status_code=500, detail="Video generation failed")
