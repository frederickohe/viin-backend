from fastapi import APIRouter, Depends, HTTPException
from another_fastapi_jwt_auth import AuthJWT
from another_fastapi_jwt_auth.exceptions import MissingTokenError
import jwt
from sqlalchemy.orm import Session
from core.agent.dto.commandreqeust import CommandRequest
from utilities.dbconfig import SessionLocal
from core.agent.agent import AutoBus
from core.agent.dto.media_generation_request import MediaGenerationRequest
from core.media.controller.media_controller import generate_image, generate_video
from core.media.dto.media_generation_response import ImageGenerationResponse, VideoGenerationResponse
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Lazy initialization - only create the agent when first needed
_viin_agent_instance = None

def get_viin_agent():
    """Lazy initialization of agent. Only created on first use."""
    global _viin_agent_instance
    if _viin_agent_instance is None:
        logger.info("Lazy initializing Viin agent on first use...")
        _viin_agent_instance = AutoBus()
    return _viin_agent_instance

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
    assistant = get_viin_agent()

    response_text = assistant.process_user_message(
        userid=query.userid,
        message=query.message,
        agent_name=query.agent_name,
        db_session=db,
    )
    return {"response": response_text}


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