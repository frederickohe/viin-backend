import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import logging
from another_fastapi_jwt_auth import AuthJWT
from core.auth.controller.authcontroller import validate_token
from core.nlu.dto.reponse.nluresponse import NLUResponse
from core.nlu.nlu import AutobusNLUSystem
from core.nlu.dto.request.nlurequest import NLURequest
from core.user.service.user_service import UserService
from utilities.dbconfig import SessionLocal
from utilities.phone_utils import normalize_ghana_phone_number

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

nlu_routes = APIRouter()

@nlu_routes.post("/process", response_model=NLUResponse)
async def process_message(
    request: NLURequest,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Process natural language messages through the NLU system.
    Requires a signed-in Viin account.
    """
    try:
        user_service = UserService(db)
        current_user = user_service.get_current_user(authjwt.get_jwt_subject())

        if not current_user.enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not verified yet. Please complete signup and OTP verification.",
            )

        account_phone = (current_user.phone or "").strip()
        if not account_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your account needs a phone number before you can use the assistant.",
            )

        if request.phone:
            requested = normalize_ghana_phone_number(request.phone)
            actual = normalize_ghana_phone_number(account_phone)
            if requested and actual and requested != actual:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Phone number does not match your signed-in account.",
                )

        nlu_system = AutobusNLUSystem(db_session=db)
        logger.info("Processing message for user %s", account_phone[:32])

        response = nlu_system.process_message(account_phone, request.message)

        return NLUResponse(
            user_id=account_phone,
            message=request.message,
            response=response,
            success=True,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing NLU message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing message: {str(e)}",
        )

@nlu_routes.get("/chat-updates")
async def get_chat_updates(
    since: str | None = None,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Return assistant messages (including due-task reminders) for the web chat UI.
    Pass ``since`` as an ISO timestamp to receive only newer messages.
    """
    try:
        user_service = UserService(db)
        current_user = user_service.get_current_user(authjwt.get_jwt_subject())
        if not current_user.phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Your account needs a phone number to use chat reminders.",
            )

        nlu_system = AutobusNLUSystem(db_session=db)
        state = nlu_system.conversation_manager.get_conversation_state(current_user.phone)
        history = state.conversation_history or []
        assistant_messages = [
            m for m in history
            if (m.get("role") == "assistant")
        ]
        if since:
            assistant_messages = [
                m for m in assistant_messages
                if (m.get("timestamp") or "") > since
            ]

        return {
            "user_id": current_user.phone,
            "messages": assistant_messages,
            "success": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat updates: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching chat updates: {str(e)}",
        )


@nlu_routes.get("/conversation-history")
async def get_conversation_history(
    user_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Get user's conversation history
    """
    try:
        user_service = UserService(db)
        current_user = user_service.get_current_user(authjwt.get_jwt_subject())

        if normalize_ghana_phone_number(user_id) != normalize_ghana_phone_number(current_user.phone or ""):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        nlu_system = AutobusNLUSystem(db_session=db)
        conversation_state = nlu_system.conversation_manager.get_conversation_state(current_user.phone)

        return {
            "user_id": current_user.phone,
            "conversation_history": conversation_state.conversation_history,
            "current_intent": conversation_state.current_intent,
            "collected_slots": conversation_state.collected_slots,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching conversation history: {str(e)}",
        )

@nlu_routes.delete("/conversation-history")
async def clear_conversation_history(
    user_id: str,
    authjwt: AuthJWT = Depends(validate_token),
    db: Session = Depends(get_db),
):
    """
    Clear user's conversation history
    """
    try:
        user_service = UserService(db)
        current_user = user_service.get_current_user(authjwt.get_jwt_subject())

        if normalize_ghana_phone_number(user_id) != normalize_ghana_phone_number(current_user.phone or ""):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

        nlu_system = AutobusNLUSystem(db_session=db)
        nlu_system.conversation_manager.reset_conversation_state(current_user.phone)

        return {
            "success": True,
            "message": "Conversation history cleared successfully",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error clearing conversation history: {str(e)}",
        )

@nlu_routes.get("/health")
async def health_check():
    """
    Health check for NLU service
    """
    return {
        "status": "healthy",
        "service": "Autobus NLU System",
        "timestamp": f"{datetime.datetime.utcnow().isoformat()}Z",
    }
