from fastapi import APIRouter, Depends, HTTPException
from another_fastapi_jwt_auth import AuthJWT
from another_fastapi_jwt_auth.exceptions import MissingTokenError
import jwt
from sqlalchemy.orm import Session
from core.agent.dto.commandreqeust import CommandRequest
from utilities.dbconfig import SessionLocal
from core.agent.agent import AutoBus
from core.agent.dto.media_generation_request import MediaGenerationRequest
from core.credits.model.credit_types import CreditType
from core.credits.service.credit_service import CreditService
from core.media.controller.media_controller import generate_image, generate_video
from core.user.service.user_service import UserService
from core.media.dto.media_generation_response import ImageGenerationResponse, VideoGenerationResponse
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Lazy initialization - only create the agent when first needed
_autobus_agent_instance = None

def get_autobus_agent():
    """Lazy initialization of AutoBus agent. Only created on first use."""
    global _autobus_agent_instance
    if _autobus_agent_instance is None:
        logger.info("Lazy initializing AutoBus agent on first use...")
        _autobus_agent_instance = AutoBus()
    return _autobus_agent_instance

def validate_token(authjwt: AuthJWT = Depends()):
    try:
        authjwt.jwt_required()
        return authjwt
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401, 
            detail="Token expired. Please log in again."
        )
    except MissingTokenError:
        raise HTTPException(
            status_code=401,
            detail="No token found. Please create an account and log in.",
        )
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {str(e)}"
        )
    
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
    
agent_routes = APIRouter()

@agent_routes.post("/command")
def agent(query: CommandRequest, db: Session = Depends(get_db)):
    credit_service = CreditService(db)
    user_id = credit_service.resolve_user_id(query.userid)
    if user_id:
        credit_service.require_credits(user_id, CreditType.LLM.value, 1.0, "agent_command")
    else:
        user = UserService(db).get_user_by_phone(query.userid)
        if user:
            credit_service.require_credits(user.id, CreditType.LLM.value, 1.0, "agent_command")

    assistant = get_autobus_agent()
    
    return assistant.process_user_message(
        userid=query.userid,
        message=query.message,
        agent_name=query.agent_name
    )


@agent_routes.post("/generate-image", response_model=ImageGenerationResponse)
async def agent_generate_image(
    req: MediaGenerationRequest,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    return await generate_image(req, db=db, authjwt=authjwt)


@agent_routes.post("/generate-video", response_model=VideoGenerationResponse)
async def agent_generate_video(
    req: MediaGenerationRequest,
    store: bool = False,
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    return await generate_video(req, store=store, db=db, authjwt=authjwt)