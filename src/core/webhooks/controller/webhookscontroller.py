from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
import json
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from core.auth.service.sessiondriver import SessionDriver, TokenData
from another_fastapi_jwt_auth import AuthJWT
from core.exceptions import *
from core.webhooks.dto.request.dialogrequest import DialogRequest
from core.webhooks.dto.request.simple_chat_request import SimpleChatRequest
from core.webhooks.dto.response.simple_chat_response import SimpleChatResponse
from utilities.dbconfig import SessionLocal
from sqlalchemy.orm import Session
import logging
import os
from core.user.model.User import User
from core.nlu.nlu import AutobusNLUSystem
from core.subscription.service.subscription_service import SubscriptionService
from core.webhooks.service.whatsapp_service import WhatsAppService
from utilities.phone_utils import normalize_ghana_phone_number
from core.filterpipe.filter import FilterPipeline

# DTO Models
from core.notification.dto.response.message_response import MessageResponse
from core.webhooks.dto.response.message_response import AutobusResponse

from another_fastapi_jwt_auth.exceptions import MissingTokenError
from core.auth.service.authservice import AuthService

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Reuse your existing token validation and DB dependencies
from core.user.controller.usercontroller import validate_token, get_db

# Controller (Router)
webhooks_routes = APIRouter()

@webhooks_routes.get("/start-dialog")
def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
    verify_token: Optional[str] = Query(None, alias="hub.verify_token")
):
    """
    Webhook verification endpoint for Meta (Facebook/WhatsApp) webhooks.
    Meta will send a GET request with hub.mode, hub.challenge, and hub.verify_token.
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

@webhooks_routes.post("/start-dialog")
async def start_dialog(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Handles incoming webhooks from either:
    1. Meta (Facebook/WhatsApp) webhooks - with 'object' and 'entry' fields
    2. Simple chat requests from Flutter app - with 'userid' and 'message' fields
    
    Routes to appropriate handler based on webhook type.
    """
    # Parse the incoming payload as generic dict
    payload = await request.json()

    # Log the incoming webhook payload
    logger.info(f"Received webhook payload: {json.dumps(payload, indent=2)}")

    try:
        # Detect if this is a simple chat request (Flutter app)
        if "userid" in payload and "message" in payload:
            # This is a simple direct chat request from Flutter app
            logger.info("Detected simple chat request from Flutter app")
            return await handle_simple_chat(
                userid=payload.get("userid"),
                message=payload.get("message"),
                context=payload.get("context"),
                db=db
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


async def handle_simple_chat(userid: str, message: str, context: str, db: Session):
    """
    Handles simple chat requests from Flutter app or other direct clients.
    Processes the message through NLU and returns the response directly.
    """
    try:
        logger.info(f"Processing simple chat message from userid: {userid}")

        nlu_system = AutobusNLUSystem()

        # Process the message
        response_message = nlu_system.process_message(
                userid,
                message
        )

        logger.info(f"Generated response: {response_message}")
        
        return SimpleChatResponse(message=response_message)

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

    # Check if user exists in database
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
        nlu_system = AutobusNLUSystem()

        # Process the message
        response_message = nlu_system.process_message(
                phone,
                message_text
        )

        logger.info(f"Generated response: {response_message}")

        # Send the response back to the user via WhatsApp
        message_sent = whatsapp_service.send_message(
            phone_id=phone_id,
            recipient_phone=phone,
            message_text=response_message
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
            response_message = nlu_system.process_message(
                phone,
                user_message,
                result["has_active_subscription"],
                image_media_id=media_id
            )
            
            logger.info(f"Generated response for image message: {response_message}")
            
            # Send the response back to the user
            message_sent = whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text=response_message
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
            response_message = nlu_system.process_message(
                phone,
                user_message,
                result["has_active_subscription"],
                audio_media_id=media_id
            )
            
            logger.info(f"Generated response for audio message: {response_message}")
            
            # Send the response back to the user
            message_sent = whatsapp_service.send_message(
                phone_id=phone_id,
                recipient_phone=phone,
                message_text=response_message
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
