import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Dict, Any
import logging
from core.auth.service.authservice import AuthService
from core.nlu.dto.reponse.nluresponse import NLUResponse
from core.nlu.nlu import AutobusNLUSystem
from core.nlu.dto.request.nlurequest import NLURequest
from core.subscription.service.subscription_service import SubscriptionService
from core.user.service.user_service import UserService
from utilities.dbconfig import SessionLocal

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Initialize NLU system
nlu_system = AutobusNLUSystem()

nlu_routes = APIRouter()

@nlu_routes.post("/process", response_model=NLUResponse)
async def process_message(
    request: NLURequest,
    db: Session = Depends(get_db)
):
    """
    Process natural language messages through the NLU system
    """
    try:
        # Get current user from request
        db = SessionLocal()
        user_service = UserService(db)

        current_user = user_service.get_user_by_phone(request.phone)

        # Get user subscription status from database
        subscription_service = SubscriptionService(db)

        result = subscription_service.get_user_subscription_status_by_phone(request.phone)

        logger.info(f"Processing message for user {current_user.username}: {request.message}")
        
        # Process message through NLU system
        #nlu_system.initialize_user(current_user.phone, current_user.hashed_pin)

        response = nlu_system.process_message(
            current_user.phone,
            request.message,
            result["has_active_subscription"]
        )

        return NLUResponse(
            user_id=current_user.phone,
            message=request.message,
            response=response,
            success=True
        )
        
    except Exception as e:
        logger.error(f"Error processing NLU message: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error processing message: {str(e)}"
        )

@nlu_routes.get("/conversation-history")
async def get_conversation_history(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Get user's conversation history
    """
    try:

        # Get current user from request
        db = SessionLocal()
        user_service = UserService(db)

        current_user = user_service.get_user_by_phone(user_id)

        
        conversation_state = nlu_system.conversation_manager.get_conversation_state(current_user)
        
        return {
            "user_id": current_user,
            "conversation_history": conversation_state.conversation_history,
            "current_intent": conversation_state.current_intent,
            "collected_slots": conversation_state.collected_slots
        }
        
    except Exception as e:
        logger.error(f"Error fetching conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching conversation history: {str(e)}"
        )

@nlu_routes.delete("/conversation-history")
async def clear_conversation_history(
    user_id: str,
    db: Session = Depends(get_db)
):
    """
    Clear user's conversation history
    """
    try:
        # Get current user from request
        db = SessionLocal()
        user_service = UserService(db)

        current_user = user_service.get_user_by_phone(user_id)
        
        nlu_system.conversation_manager.reset_conversation_state(current_user)
        
        return {
            "success": True,
            "message": "Conversation history cleared successfully"
        }
        
    except Exception as e:
        logger.error(f"Error clearing conversation history: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error clearing conversation history: {str(e)}"
        )

@nlu_routes.get("/health")
async def health_check():
    """
    Health check for NLU service
    """
    return {
        "status": "healthy",
        "service": "Autobus NLU System",
        "timestamp": f"{datetime.datetime.utcnow().isoformat()}Z"
    }