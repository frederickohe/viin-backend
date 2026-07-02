from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from another_fastapi_jwt_auth import AuthJWT

from core.user.controller.usercontroller import validate_token, get_db
from core.conversationmanager.service.conversation_list_service import ConversationListService
from core.interventions.service.intervention_service import InterventionService
from core.nlu.service.conversation_manager import ConversationManager


intervention_routes = APIRouter()


@intervention_routes.post("/create")
def create_intervention(
    trigger: str = Query(..., description="Reason class e.g. explicit_user_request, unknown_intent, execution_error"),
    reason: Optional[str] = Query(None, description="Human-readable reason"),
    conversation_date: Optional[str] = Query(None, description="ISO date; defaults to today"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    user_id = authjwt.get_jwt_subject()
    svc = InterventionService(db)
    cm = ConversationManager()

    conv_date = date.fromisoformat(conversation_date) if conversation_date else date.today()
    intervention = svc.create_intervention(
        user_id=user_id,
        trigger=trigger,
        reason=reason,
        conversation_date=conv_date,
    )

    # Activate intervention mode in conversation state so the bot pauses.
    state = cm.get_conversation_state(user_id)
    state.intervention_active = True
    state.intervention_id = int(intervention.id)
    state.intervention_trigger = trigger
    state.intervention_reason = reason
    state.intervention_created_at = (intervention.created_at.isoformat() if intervention.created_at else None)
    cm._save_conversation_state(state)

    return {
        "success": True,
        "intervention": {
            "id": intervention.id,
            "user_id": intervention.user_id,
            "conversation_date": str(intervention.conversation_date),
            "status": intervention.status,
            "trigger": intervention.trigger,
            "reason": intervention.reason,
            "created_at": intervention.created_at.isoformat() if intervention.created_at else None,
        },
    }


@intervention_routes.get("/list")
def list_interventions(
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    user_id = authjwt.get_jwt_subject()
    svc = InterventionService(db)
    items = svc.list_interventions(user_id=user_id, status=status_filter, limit=limit)
    return {
        "items": [
            {
                "id": i.id,
                "conversation_date": str(i.conversation_date),
                "status": i.status,
                "trigger": i.trigger,
                "reason": i.reason,
                "created_at": i.created_at.isoformat() if i.created_at else None,
                "closed_at": i.closed_at.isoformat() if i.closed_at else None,
            }
            for i in items
        ]
    }


@intervention_routes.get("/daily-conversation")
def get_daily_conversation(
    conversation_date: Optional[str] = Query(None, description="ISO date; defaults to today"),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    user_id = authjwt.get_jwt_subject()
    cm = ConversationManager()

    # Current implementation stores "today" only; return that state and metadata.
    # If you need historical access by date, we can extend ConversationManager to load by date.
    state = cm.get_conversation_state(user_id)
    return {
        "user_id": user_id,
        "conversation_date": str(state.conversation_date),
        "intervention_active": bool(state.intervention_active),
        "intervention_id": state.intervention_id,
        "conversation_history": state.conversation_history,
        "current_intent": state.current_intent,
        "collected_slots": state.collected_slots,
    }


@intervention_routes.post("/human-message")
def send_human_message(
    message: str = Query(..., min_length=1),
    session_id: Optional[int] = Query(
        None,
        description="Daily conversation session id; when set, message is stored on that session.",
    ),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    """
    Records a human/agent message into the daily conversation history.
    Delivery to external channels (e.g. WhatsApp) can be performed by the calling app.
    """
    user_id = authjwt.get_jwt_subject()

    if session_id is not None:
        service = ConversationListService(db)
        detail = service.append_human_message_to_session(user_id, int(session_id), message)
        if not detail:
            existing = service.get_session_detail(user_id, int(session_id))
            if not existing:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conversation not found",
                )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No active intervention for this conversation.",
            )
        return {"success": True, "conversation": detail.model_dump()}

    cm = ConversationManager()
    state = cm.get_conversation_state(user_id)

    if not state.intervention_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active intervention for this conversation.",
        )

    cm.update_conversation_history(user_id, "human", message)
    return {"success": True}


@intervention_routes.post("/close")
def close_intervention(
    intervention_id: int = Query(...),
    db: Session = Depends(get_db),
    authjwt: AuthJWT = Depends(validate_token),
):
    user_id = authjwt.get_jwt_subject()
    svc = InterventionService(db)
    cm = ConversationManager()

    closed = svc.close_intervention(intervention_id=intervention_id, user_id=user_id)

    # Turn the bot back on for the day.
    state = cm.get_conversation_state(user_id)
    if state.intervention_id == int(intervention_id):
        state.intervention_active = False
        state.intervention_trigger = None
        state.intervention_reason = None
        cm._save_conversation_state(state)

    return {"success": True, "status": closed.status, "closed_at": closed.closed_at.isoformat() if closed.closed_at else None}

