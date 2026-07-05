from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
import json
from datetime import datetime
from typing import Optional, Tuple
from core.webhooks.dto.response.simple_chat_response import SimpleChatResponse
from utilities.dbconfig import get_db
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
import logging
import os
from core.user.model.User import User
from core.user.service.user_service import UserService
from core.nlu.nlu import AutobusNLUSystem
from core.nlu.service.message_delivery import send_whatsapp_nlu_response
from core.subscription.service.subscription_service import SubscriptionService
from core.webhooks.service.whatsapp_service import WhatsAppService
from utilities.phone_utils import normalize_ghana_phone_number
from core.auth.service.authservice import AuthService

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Public routes: no JWT / Bearer dependency (external providers & simple chat clients).
webhooks_routes = APIRouter()

@webhooks_routes.get("/start-dialog")
def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
    verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
):
    """
    Webhook verification endpoint for Meta (Facebook/WhatsApp) webhooks.
    Meta will send a GET request with hub.mode, hub.challenge, and hub.verify_token.

    Does not require application JWT authentication (Meta uses hub.verify_token).
    """
    expected_verify_token = os.getenv("VERIFY_TOKEN")

    if mode == "subscribe" and verify_token == expected_verify_token:
        logger.info("WEBHOOK VERIFIED")
        return int(challenge)
    else:
        logger.warning("Webhook verification failed")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification failed"
        )

def _payload_str(payload: dict, *keys: str) -> str:
    for k in keys:
        v = payload.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _merchant_display_name(user: User) -> str:
    return (
        user.company
        or user.organization_workplace
        or user.fullname
        or user.id
        or ""
    ).strip()


def _company_match_score(user: User, needle: str) -> int:
    """Higher = better match for public company lookup."""
    n = needle.lower()
    fields = [
        (user.company, 100),
        (user.organization_workplace, 90),
        (user.fullname, 80),
    ]
    best = 0
    for raw, exact_weight in fields:
        val = (raw or "").strip().lower()
        if not val:
            continue
        if val == n:
            best = max(best, exact_weight)
        elif val.startswith(n):
            best = max(best, exact_weight - 15)
        elif n in val:
            best = max(best, exact_weight - 35)
    return best


def _find_company_matches(db: Session, name: str, *, limit: int = 8) -> list[User]:
    needle = name.strip().lower()
    if len(needle) < 2:
        return []

    like = f"%{needle}%"
    candidates = (
        db.query(User)
        .filter(
            or_(
                func.lower(User.company).like(like),
                func.lower(User.organization_workplace).like(like),
                func.lower(User.fullname).like(like),
            )
        )
        .limit(50)
        .all()
    )
    if not candidates:
        return []

    scored = [(u, _company_match_score(u, needle)) for u in candidates]
    scored = [(u, s) for u, s in scored if s > 0]
    scored.sort(key=lambda pair: (-pair[1], _merchant_display_name(pair[0]).lower()))

    # Single clear winner: exact match on company name, or lone high-confidence hit.
    if len(scored) == 1:
        return [scored[0][0]]

    top_score = scored[0][1]
    second_score = scored[1][1] if len(scored) > 1 else 0
    if top_score >= 100 and top_score - second_score >= 10:
        return [scored[0][0]]
    if top_score >= 90 and second_score < 70:
        return [scored[0][0]]

    return [u for u, _ in scored[:limit]]


def _resolve_company_number(
    db: Session,
    *,
    company_number: str = "",
    company_name: str = "",
) -> Tuple[str, Optional[str]]:
    """
    Resolve merchant ``users.id`` from an explicit id or a display name.
    Returns (company_id, error_message).
    """
    comp = (company_number or "").strip()
    if comp:
        merchant = db.query(User).filter(User.id == comp).first()
        if not merchant:
            return "", f"Unknown company_number: no merchant user with id '{comp}'."
        return comp, None

    name = (company_name or "").strip()
    if not name:
        return "", "company_number or company_name is required."

    matches = _find_company_matches(db, name)
    if not matches:
        return "", f"No business found matching '{name}'."
    if len(matches) > 1:
        labels = ", ".join(_merchant_display_name(u) for u in matches[:5])
        return "", f"Multiple businesses match '{name}': {labels}. Please pick one from the list."
    return matches[0].id, None


@webhooks_routes.get("/company-lookup")
def company_lookup(
    name: str = Query(..., min_length=2, max_length=200),
    db: Session = Depends(get_db),
):
    """Public helper for the marketing-site chatbot to validate a business name."""
    query = name.strip()
    matches = _find_company_matches(db, query)
    if not matches:
        return {"ok": False, "message": f"No business found matching '{query}'."}

    options = [
        {
            "company_number": u.id,
            "display_name": _merchant_display_name(u),
        }
        for u in matches
    ]

    if len(matches) == 1:
        u = matches[0]
        return {
            "ok": True,
            "company_number": u.id,
            "display_name": _merchant_display_name(u),
            "matches": options,
        }

    return {
        "ok": True,
        "requires_selection": True,
        "message": f"Several businesses match '{query}'. Pick the one you mean.",
        "matches": options,
    }


@webhooks_routes.post("/start-dialog")
async def start_dialog(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handles incoming webhooks from either:
    1. Meta (Facebook/WhatsApp) webhooks - with 'object' and 'entry' fields
    2. Simple chat — preferred shape: ``customer_number``, ``company_number`` (merchant ``users.id``),
       ``message``
    3. Legacy simple chat: ``userid`` + ``message`` (optional ``context``; NLU currently ignores it)

    Routes to appropriate handler based on webhook type.

    Does not require application JWT authentication.
    """
    # Parse the incoming payload as generic dict
    payload = await request.json()

    # Log the incoming webhook payload
    logger.info(f"Received webhook payload: {json.dumps(payload, indent=2)}")

    try:
        customer = _payload_str(payload, "customer_number", "customer_phone", "customer")
        company = _payload_str(payload, "company_number", "company_id", "merchant_id")
        company_name = _payload_str(
            payload, "company_name", "company", "business_name", "merchant_name"
        )
        msg = _payload_str(payload, "message", "webhook_message", "text", "body")

        if customer and msg and (company or company_name):
            resolved_company, company_err = _resolve_company_number(
                db,
                company_number=company,
                company_name=company_name,
            )
            if company_err:
                return SimpleChatResponse(message=company_err)
            logger.info("Detected simple chat request (customer + company + message)")
            return await handle_simple_chat(
                customer_number=customer,
                company_number=resolved_company,
                message=msg,
                db=db,
            )

        # Legacy: Flutter / older clients
        if "userid" in payload and "message" in payload:
            legacy_user = _payload_str(payload, "userid", "user_id", "phone")
            legacy_msg = _payload_str(payload, "message", "webhook_message", "text", "body")
            if legacy_user and legacy_msg:
                logger.info("Detected legacy simple chat request (userid + message)")
                return await handle_simple_chat(
                    customer_number=legacy_user,
                    company_number="",
                    message=legacy_msg,
                    db=db,
                )
        
        # Otherwise, treat as Meta WhatsApp webhook
        # Check if this is a valid Meta webhook payload
        if "object" not in payload or "entry" not in payload:
            logger.warning("Invalid webhook payload structure")
            return {"status": "ok", "message": "Invalid payload structure"}

        # Extract entry and changes
        entries = payload.get("entry", [])
        if not entries:
            logger.warning("No entries in webhook payload")
            return {"status": "ok", "message": "No entries"}

        entry = entries[0]
        changes = entry.get("changes", [])
        if not changes:
            logger.warning("No changes in webhook entry")
            return {"status": "ok", "message": "No changes"}

        change = changes[0]
        value = change.get("value", {})
        field = change.get("field", "")

        logger.info(f"Webhook field type: {field}")

        # Route based on webhook type

        # 1. Handle incoming messages (text, image, etc.)
        if "messages" in value:
            return handle_incoming_message(
                value=value,
                db=db
            )

        # 2. Handle message status updates (delivered, read, failed)
        elif "statuses" in value:
            return handle_message_status(
                value=value,
                db=db
            )

        # 3. Handle other webhook types
        else:
            logger.info(f"Unsupported webhook field: {field}")
            return {"status": "ok", "message": "Webhook type not handled"}

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error processing webhook: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error processing webhook"
        )


async def handle_simple_chat(
    customer_number: str,
    company_number: str,
    message: str,
    db: Session,
):
    """
    Simple JSON chat: ``customer_number`` (chatter id / phone), ``company_number`` (merchant ``users.id``),
    ``message``. When ``company_number`` is empty, ``customer_number`` is used alone (legacy behaviour).
    """
    try:
        cust = (customer_number or "").strip()
        comp = (company_number or "").strip()
        msg = (message or "").strip()

        if not cust or not msg:
            return SimpleChatResponse(
                message="customer_number and message are required."
            )

        if comp:
            merchant = db.query(User).filter(User.id == comp).first()
            if not merchant:
                return SimpleChatResponse(
                    message="Unknown company_number: no merchant user with that id."
                )
            nlu_user_id = f"{comp}:{normalize_ghana_phone_number(cust)}"
            logger.info(
                "Processing simple chat (scoped) company=%s customer=%s",
                comp,
                cust[:32],
            )
        else:
            nlu_user_id = normalize_ghana_phone_number(cust)
            logger.info("Processing simple chat (legacy key) customer=%s", cust[:32])

        user = UserService(db).find_user_by_phone(nlu_user_id)
        if not user:
            return SimpleChatResponse(
                message=(
                    "Hi! I don't have a Viin account for this number yet. "
                    "Create your free account on the Viin website, verify your phone, "
                    "then message me again and I'll be ready to help."
                )
            )

        nlu_system = AutobusNLUSystem(db_session=db)
        result = nlu_system.process_message(nlu_user_id, msg)

        logger.info(f"Generated response: {result.text}")

        return SimpleChatResponse(message=result.text)

    except Exception as e:
        logger.error(f"Error handling simple chat: {e}", exc_info=True)
        return SimpleChatResponse(
            message="An error occurred while processing your message. Please try again."
        )


def handle_incoming_message(value: dict, db: Session):
    """
    Handles incoming messages from users.
    Processes text messages, Flow responses, and other message types.
    """
    try:
        # Get phone number ID from metadata (needed for sending messages)
        metadata = value.get("metadata", {})
        phone_id = metadata.get("phone_id")
        if not phone_id:
            logger.error("Missing phone_id in metadata")
            return {"status": "error", "message": "Missing phone_id"}

        logger.info(f"Phone number ID: {phone_id}")

        # Get sender phone number
        contacts = value.get("contacts", [])
        if not contacts:
            logger.error("Missing contacts in webhook payload")
            return {"status": "error", "message": "Missing contacts"}

        phone = contacts[0].get("wa_id")
        if not phone:
            logger.error("Missing wa_id in contacts")
            return {"status": "error", "message": "Missing wa_id"}

        logger.info(f"Extracted phone number: {phone}")

        # Get the message
        messages = value.get("messages", [])
        if not messages:
            logger.warning("No messages in payload")
            return {"status": "ok", "message": "No messages"}

        message = messages[0]
        message_type = message.get("type")

        # Handle different message types
        if message_type == "text":
            return handle_text_message(
                message=message,
                phone=phone,
                phone_id=phone_id,
                db=db
            )

        elif message_type == "interactive":
            # This handles Flow responses
            return handle_interactive_message(
                message=message,
                phone=phone,
                phone_id=phone_id,
                db=db
            )

        elif message_type == "image":
            return handle_image_message(
                message=message,
                phone=phone,
                phone_id=phone_id,
                db=db
            )

        elif message_type == "audio":
            return handle_audio_message(
                message=message,
                phone=phone,
                phone_id=phone_id,
                db=db
            )

        elif message_type in ["video", "document"]:
            logger.info(f"Received {message_type} message from {phone}")
            whatsapp_service = WhatsAppService()
            whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text=f"Thanks for the {message_type}! I currently support text, images, and audio messages."
            )
            return {"status": "ok", "message": f"{message_type} message received"}

        else:
            logger.warning(f"Unsupported message type: {message_type}")
            whatsapp_service = WhatsAppService()
            whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text="Sorry, I don't support this message type yet."
            )
            return {"status": "ok", "message": f"Unsupported message type: {message_type}"}

    except Exception as e:
        logger.error(f"Error handling incoming message: {e}", exc_info=True)
        raise


def handle_text_message(message: dict, phone: str, phone_id: str, db: Session):
    """Handle regular text messages"""
    text_data = message.get("text")
    if not text_data or "body" not in text_data:
        logger.warning("Text message has no body")
        return {"status": "ok", "message": "Empty text message"}

    message_text = text_data.get("body")
    logger.info(f"Extracted text message: {message_text}")

    from core.user.service.user_service import UserService

    existing_user = UserService(db).find_user_by_phone(phone)
    whatsapp_service = WhatsAppService()

    if not existing_user:
        # New user - send registration template
        logger.info(f"New user detected: {phone}. Sending registration template.")
        message_sent = whatsapp_service.send_registration_template(
            phone_id=phone_id,
            recipient_phone=phone
        )

        if not message_sent:
            logger.error("Failed to send WhatsApp registration template")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send WhatsApp registration template"
            )

        return {"status": "success", "message": "Registration template sent"}

    else:
        # Existing user - process message through pipeline (which now also dispatches to AutoBus)
        logger.info(f"Existing user detected: {phone}. Processing message through NLU.")

        # message_id = message.get("id")
        # with typing_indicator_context(
        #     whatsapp_service=whatsapp_service,
        #     phone_number_id=phone_number_id,
        #     recipient_phone=phone,
        #     message_id=message_id
        # ):
        
        # Initialize NLU system and subscription service
        nlu_system = AutobusNLUSystem(db_session=db)

        # Process the message
        result = nlu_system.process_message(
                normalize_ghana_phone_number(phone),
                message_text
        )

        logger.info(f"Generated response: {result.text}")

        message_sent = send_whatsapp_nlu_response(
            whatsapp_service,
            phone_id=phone_id,
            recipient_phone=phone,
            result=result,
        )

        if not message_sent:
            logger.error("Failed to send WhatsApp message")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send WhatsApp message"
            )

        return {"status": "success", "message": "Message processed and sent"}


def handle_interactive_message(message: dict, phone: str, phone_id: str, db: Session):
    """
    Handle interactive messages like Flow responses and button replies.
    This is where you'll receive the registration form data!
    """
    interactive = message.get("interactive", {})
    interactive_type = interactive.get("type")

    logger.info(f"Received interactive message type: {interactive_type}")

    if interactive_type == "nfm_reply":
        # This is a Flow response (your registration form!)
        nfm_reply = interactive.get("nfm_reply", {})
        response_json = nfm_reply.get("response_json", "{}")
        flow_token = nfm_reply.get("flow_token")

        logger.info(f"Flow response received from {phone}")
        logger.info(f"Flow token: {flow_token}")
        logger.info(f"Response data: {response_json}")

        # Parse the registration form data
        registration_data = json.loads(response_json)

        # Log the complete registration data
        logger.info(f"Registration data received: {json.dumps(registration_data, indent=2)}")

        try:
            # Extract registration fields from Flow response
            first_name = registration_data.get("screen_0_First_Name_0", "").strip()
            last_name = registration_data.get("screen_0_Last_Name_1", "").strip()
            user_phone = registration_data.get("screen_0_phone_2", "").strip()
            email = registration_data.get("screen_0_email_3", "").strip()
            pin = registration_data.get("screen_0_PIN_4", "").strip()

            # Validate required fields
            if not all([first_name, last_name, user_phone, email, pin]):
                logger.error("Missing required fields in registration data")
                whatsapp_service = WhatsAppService()
                whatsapp_service.send_message(
                    phone_id=phone_id,
                    recipient_phone=phone,
                    message_text="Registration failed. Please ensure all fields are filled correctly."
                )
                return {"status": "error", "message": "Missing required fields"}

            # Normalize phone numbers
            normalized_user_phone = normalize_ghana_phone_number(user_phone)
            normalized_wa_id = normalize_ghana_phone_number(phone)

            logger.info(f"Phone normalization - Form: {user_phone} -> {normalized_user_phone}")
            logger.info(f"Phone normalization - WhatsApp: {phone} -> {normalized_wa_id}")

            # Check if user already exists
            existing_user = db.query(User).filter(
                (User.phone == normalized_wa_id) | (User.email == email)
            ).first()

            if existing_user:
                logger.warning(f"User already exists: {email} or {normalized_wa_id}")
                whatsapp_service = WhatsAppService()
                whatsapp_service.send_message(
                    phone_id=phone_id,
                    recipient_phone=phone,
                    message_text="You’re already registered with Lemo, so you can happily continue using the our service."
                )
                return {"status": "error", "message": "User already exists"}

            # Create user using AuthService
            auth_service = AuthService(db)

            # Generate unique user ID
            user_id = auth_service.generate_user_id()

            # Hash the PIN
            hashed_pin = auth_service.hash_password(pin)

            # Create new user
            new_user = User(
                id=user_id,
                username=email,  # Use email as username
                first_name=first_name,
                last_name=last_name,
                phone=normalized_wa_id,  # Use WhatsApp ID as primary phone
                email=email,
                hashed_pin=hashed_pin,
                enabled=True,  # Enable immediately for WhatsApp users
                created_at=datetime.now()
            )

            db.add(new_user)
            db.commit()
            db.refresh(new_user)

            logger.info(f"User registered successfully: {user_id} - {email}")

            # Send confirmation message
            whatsapp_service = WhatsAppService()
            whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text=f"🎉 Welcome {first_name}! Your registration is complete. You can now start using Autobus."
            )

        except Exception as e:
            logger.error(f"Error processing registration: {e}", exc_info=True)
            db.rollback()

            # Send error message to user
            whatsapp_service = WhatsAppService()
            whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text="Sorry, registration failed. Please try again later."
            )

            return {"status": "error", "message": f"Registration failed: {str(e)}"}

        return {"status": "success", "message": "Flow response processed"}

    elif interactive_type == "button_reply":
        # Handle button replies
        button_reply = interactive.get("button_reply", {})
        button_id = button_reply.get("id")
        button_text = button_reply.get("title")

        logger.info(f"Button clicked: {button_id} - {button_text}")

        # Process button action
        # ...

        return {"status": "success", "message": "Button reply processed"}

    else:
        logger.warning(f"Unsupported interactive type: {interactive_type}")
        return {"status": "ok", "message": f"Unsupported interactive type: {interactive_type}"}


def handle_message_status(value: dict, db: Session):
    """
    Handle message status updates (sent, delivered, read, failed).
    Useful for tracking message delivery and updating your database.
    """
    try:
        statuses = value.get("statuses", [])

        for status_update in statuses:
            message_id = status_update.get("id")
            status_type = status_update.get("status")  # sent, delivered, read, failed
            timestamp = status_update.get("timestamp")
            recipient_id = status_update.get("recipient_id")

            logger.info(f"Message {message_id} to {recipient_id}: {status_type}")

            if status_type == "failed":
                # Handle failed message
                errors = status_update.get("errors", [])
                if errors:
                    error = errors[0]
                    error_code = error.get("code")
                    error_title = error.get("title")
                    logger.error(f"Message failed: {error_code} - {error_title}")

            elif status_type == "read":
                # Message was read by recipient
                logger.info(f"Message {message_id} was read")

            # TODO: Update message status in your database
            # db.query(Message).filter(Message.whatsapp_id == message_id).update({
            #     "status": status_type,
            #     "delivered_at": timestamp if status_type == "delivered" else None,
            #     "read_at": timestamp if status_type == "read" else None
            # })
            # db.commit()

        return {"status": "success", "message": "Status updates processed"}

    except Exception as e:
        logger.error(f"Error handling message status: {e}", exc_info=True)
        raise


def handle_image_message(message: dict, phone: str, phone_id: str, db: Session):
    """
    Handle image messages from users.
    Images are processed by the LLM vision API for visual understanding.
    """
    try:
        image_data = message.get("image", {})
        media_id = image_data.get("id")
        
        if not media_id:
            logger.warning("Image message has no media ID")
            return {"status": "ok", "message": "Image received but no media ID"}
        
        logger.info(f"Received image message from {phone}, media_id: {media_id}")
        
        # Check if user exists
        existing_user = db.query(User).filter(User.phone == phone).first()
        whatsapp_service = WhatsAppService()
        
        if not existing_user:
            # New user - send registration template
            logger.info(f"New user detected: {phone}. Sending registration template.")
            message_sent = whatsapp_service.send_registration_template(
                phone_id=phone_id,
                recipient_phone=phone
            )
            
            if not message_sent:
                logger.error("Failed to send WhatsApp registration template")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to send WhatsApp registration template"
                )
            
            return {"status": "success", "message": "Registration template sent"}
        
        else:
            # Existing user - process image through NLU
            logger.info(f"Processing image for existing user: {phone}")
            
            # Initialize NLU system
            nlu_system = AutobusNLUSystem()
            subscription_service = SubscriptionService(db)
            
            # Get user subscription status
            result = subscription_service.get_user_subscription_status_by_phone(phone)
            
            # Check if image has a caption
            caption = image_data.get("caption", "").strip()
            
            if caption:
                # Use the caption as the user message
                user_message = caption
                logger.info(f"Image caption found: {caption}")
            else:
                # Default message if no caption provided
                user_message = "I am providing you with an image. The image is referenced below, use it to infer the user's intent and extract slots."
                logger.info("No caption provided with image, using default message")
            
            # Process the message with image
            nlu_result = nlu_system.process_message(
                phone,
                user_message,
                image_media_id=media_id,
            )
            
            logger.info(f"Generated response for image message: {nlu_result.text}")
            
            message_sent = send_whatsapp_nlu_response(
                whatsapp_service,
                phone_id=phone_id,
                recipient_phone=phone,
                result=nlu_result,
            )
            
            if not message_sent:
                logger.error("Failed to send WhatsApp message")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to send WhatsApp message"
                )
            
            return {"status": "success", "message": "Image processed and response sent"}
    
    except Exception as e:
        logger.error(f"Error handling image message: {e}", exc_info=True)
        raise


def handle_audio_message(message: dict, phone: str, phone_id: str, db: Session):
    """
    Handle audio messages from users.
    Audio is transcribed through the configured Groq transcription model and processed as text.
    """
    try:
        audio_data = message.get("audio", {})
        media_id = audio_data.get("id")
        
        if not media_id:
            logger.warning("Audio message has no media ID")
            return {"status": "ok", "message": "Audio received but no media ID"}
        
        logger.info(f"Received audio message from {phone}, media_id: {media_id}")
        
        # Check if user exists
        existing_user = db.query(User).filter(User.phone == phone).first()
        whatsapp_service = WhatsAppService()
        
        if not existing_user:
            # New user - send registration template
            logger.info(f"New user detected: {phone}. Sending registration template.")
            message_sent = whatsapp_service.send_registration_template(
                phone_id=phone_id,
                recipient_phone=phone
            )
            
            if not message_sent:
                logger.error("Failed to send WhatsApp registration template")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to send WhatsApp registration template"
                )
            
            return {"status": "success", "message": "Registration template sent"}
        
        else:
            # Existing user - process audio through NLU
            logger.info(f"Processing audio for existing user: {phone}")
            
            # Initialize NLU system
            nlu_system = AutobusNLUSystem()
            subscription_service = SubscriptionService(db)
            
            # Get user subscription status
            result = subscription_service.get_user_subscription_status_by_phone(phone)
            
            # Check if audio has a caption
            caption = audio_data.get("caption", "").strip()
            
            if caption:
                # Use the caption as the user message
                user_message = caption
                logger.info(f"Audio caption found: {caption}")
            else:
                # Default message (will be enhanced with transcription in NLU)
                user_message = "I'm sending you an audio message."
                logger.info("No caption provided with audio, using default message")
            
            # Process the message with audio
            nlu_result = nlu_system.process_message(
                phone,
                user_message,
                audio_media_id=media_id,
            )
            
            logger.info(f"Generated response for audio message: {nlu_result.text}")
            
            message_sent = send_whatsapp_nlu_response(
                whatsapp_service,
                phone_id=phone_id,
                recipient_phone=phone,
                result=nlu_result,
            )
            
            if not message_sent:
                logger.error("Failed to send WhatsApp message")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to send WhatsApp message"
                )
            
            return {"status": "success", "message": "Audio transcribed and response sent"}
    
    except Exception as e:
        logger.error(f"Error handling audio message: {e}", exc_info=True)
        raise
