
import base64
import os
from dataclasses import dataclass
from decimal import Decimal
import io
import re
from core.cloudstorage.service.storageservice import StorageService, StorageFolder
from core.histories.service.historyservice import HistoryService
import openai
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi import HTTPException
from core.auth.service.authservice import AuthService
from core.nlu.config import INTENT_CATEGORIES
from core.chatwoot.model.ChatwootAccount import ChatwootAccount
from core.chatwoot.service.chatwoot_api_service import ChatwootAccountClient
from core.nlu.service.intentprocessor import IntentProcessor
from core.nlu.service.intents import IntentDetector
from core.nlu.service.slot_manager import SlotManager
from core.nlu.service.conversation_manager import ConversationManager
from core.nlu.service.intent_handler_result import IntentHandlerResult
from core.nlu.service.security import SecurityManager
from core.nlu.service.date_selection_manager import DateSelectionManager, DateOption
from core.nlu.emitters.response import ResponseFormatter
from core.receipts.service.image_gen import ReceiptGenerator
from core.user.service.user_service import UserService
from utilities.dbconfig import SessionLocal
from core.auth.dto.request.user_create import UserCreateRequest
from core.receipts.service.receipt_service import ReceiptService
from core.payments.dto.paymentdto import PaymentDto
from core.payments.model.paymentmethod import PaymentMethod
from core.payments.model.paymentstatus import PaymentStatus
from core.payments.model.paynetwork import Network
from core.payments.service.paymentservice import PaymentService
from core.user.model.User import User
from utilities.uniqueidgenerator import UniqueIdGenerator
from decimal import Decimal
from core.customers.utility.network_detector import NetworkDetector
from utilities.crypto import decrypt_secret
from core.rag.conversation_vector_client import ConversationVectorClient
from core.rag.tenant import resolve_effective_rag_tenant_id


logger = logging.getLogger(__name__)

@dataclass
class ReceiptData:
    transaction_id: str
    user_id: str
    transaction_type: str
    amount: str
    status: str
    sender: str
    receiver: str
    payment_method: str
    timestamp: datetime
    # Optional loan fields
    interest_rate: Optional[str] = None
    loan_period: Optional[str] = None
    expected_pay_date: Optional[str] = None
    penalty_rate: Optional[str] = None

class AutobusNLUSystem:
    def __init__(self, db_session=None):
        self.intent_detector = IntentDetector()
        self.slot_manager = SlotManager()
        self.conversation_manager = ConversationManager()
        self.security_manager = SecurityManager()
        self.response_formatter = ResponseFormatter()
        self.intent_processor = IntentProcessor(db_session=db_session)
        self.date_selection_manager = DateSelectionManager()
        self.db_session = db_session
        self._conversation_rag = ConversationVectorClient()

    @staticmethod
    def _is_declining_more_help(text: str) -> bool:
        t = (text or "").lower().strip()
        if not t:
            return False
        phrases = (
            "no",
            "nope",
            "nah",
            "no thanks",
            "no thank you",
            "that's all",
            "thats all",
            "that is all",
            "nothing else",
            "nothing more",
            "not really",
            "i'm good",
            "im good",
            "all good",
            "that's it",
            "thats it",
            "we're done",
            "were done",
            "that's fine",
            "thats fine",
            "bye",
            "goodbye",
            "no more",
        )
        if t in phrases:
            return True
        return any(t.startswith(p + " ") or t.startswith(p + ",") for p in phrases if len(p) > 2)

    @staticmethod
    def _looks_like_fresh_order_request(text: str) -> bool:
        """Heuristic: user is starting / restarting an order without naming a product in this message."""
        t = (text or "").lower()
        needles = (
            "want to create an order",
            "create an order",
            "place an order",
            "make an order",
            "new order",
            "start an order",
            "open an order",
            "i want an order",
            "i want to order",
            "need an order",
            "would like to create an order",
            "like to place an order",
            "book an order",
        )
        return any(n in t for n in needles)

    def _apply_create_order_slot_hygiene(
        self,
        *,
        previous_intent: str,
        intent: str,
        validated_slots: Dict[str, Any],
        collected_slots: Dict[str, Any],
        user_message: str,
    ) -> None:
        """
        Prevent create_order from reusing stale item_name/quantity (and related draft fields)
        merged from older turns when the user only expresses intent to order.
        """
        if intent != "create_order":
            return
        order_keys = (
            "item_name",
            "quantity",
            "unit_price",
            "subtotal_amount",
            "discount_amount",
            "tax_amount",
            "shipping_amount",
            "customer_name",
            "customer_email",
            "customer_location",
            "customer_phone",
        )
        prev = (previous_intent or "").strip()
        vs = validated_slots or {}
        if prev != "create_order":
            for k in list(order_keys):
                if k not in vs and k in collected_slots:
                    del collected_slots[k]
            return
        if (
            "item_name" not in vs
            and "quantity" not in vs
            and self._looks_like_fresh_order_request(user_message)
        ):
            collected_slots.pop("item_name", None)
            collected_slots.pop("quantity", None)

    def _conversation_completion_tool(self, user_id: str, success_message: str) -> str:
        """After a fulfilled intent (HTTP 200): keep success text and prompt for more help."""
        state = self.conversation_manager.get_conversation_state(user_id)
        state.conversation_lifecycle = "awaiting_followup_help"
        self.conversation_manager._save_conversation_state(state)
        follow = "\n\nIs there anything else I can help you with?"
        return f"{(success_message or '').strip()}{follow}"

    def _terminal_listener_apply(self, user_id: str, outcome: IntentHandlerResult) -> str:
        if outcome.http_status == 200:
            return self._conversation_completion_tool(user_id, outcome.message)
        return outcome.message

    def _activate_intervention(
        self,
        *,
        user_id: str,
        trigger: str,
        reason: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Creates (or reuses) an open intervention for today and flips the daily conversation state into
        intervention mode so automation pauses until closed.
        """
        try:
            from core.interventions.service.intervention_service import InterventionService

            db = SessionLocal()
            svc = InterventionService(db)
            state = self.conversation_manager.get_conversation_state(user_id)
            intervention = svc.create_intervention(
                user_id=user_id,
                trigger=trigger,
                reason=reason or None,
                conversation_date=state.conversation_date,
                metadata=metadata or {},
            )

            state.intervention_active = True
            state.intervention_id = int(intervention.id)
            state.intervention_trigger = trigger
            state.intervention_reason = reason or None
            state.intervention_created_at = (
                intervention.created_at.isoformat() if intervention.created_at else datetime.utcnow().isoformat()
            )
            self.conversation_manager._save_conversation_state(state)
        except Exception as e:
            logger.warning("[INTERVENTIONS] Failed to activate intervention for %s: %s", user_id, e, exc_info=True)
        finally:
            try:
                db.close()
            except Exception:
                pass
    
    def process_message(
        self, 
        user_id: str, 
        user_message: str, 
        image_media_id: Optional[str] = None,
        image_url: Optional[str] = None,
        audio_media_id: Optional[str] = None,
        audio_url: Optional[str] = None
    ) -> str:
        """
        Main method to process user messages with optional multimodal inputs (images/audio)
        
        Args:
            user_id: User identifier
            user_message: Text message from user
            image_media_id: WhatsApp media ID for image
            image_url: Direct URL to image
            audio_media_id: WhatsApp media ID for audio
            audio_url: Direct URL to audio
        """
        # Get conversation state
        state = self.conversation_manager.get_conversation_state(user_id)

        # If a human intervention is active, pause automation unless the user explicitly ends it.
        # (We still record user messages into history below.)
        intervention_is_active = bool(getattr(state, "intervention_active", False))

        logger.info("Received message from %s: %s", user_id, (user_message or "")[:200])

        if state.conversation_lifecycle == "awaiting_followup_help":
            self.conversation_manager.update_conversation_history(user_id, "user", user_message)
            if self._is_declining_more_help(user_message):
                thanks = "You're welcome. Reach out anytime you need help."
                self.conversation_manager.update_conversation_history(user_id, "assistant", thanks)
                self.conversation_manager.finalize_completed_session(user_id)
                return thanks
            state.conversation_lifecycle = "active"
            self.conversation_manager._save_conversation_state(state)
        else:
            self.conversation_manager.update_conversation_history(user_id, "user", user_message)

        # Process multimodal inputs (images/audio)
        media_context = {}
        if image_media_id or image_url or audio_media_id or audio_url:
            logger.info("Processing media inputs for user %s", user_id)
            media_context = self._process_media_inputs(
                user_id,
                image_media_id=image_media_id,
                image_url=image_url,
                audio_media_id=audio_media_id,
                audio_url=audio_url
            )
        
        # Detect intent and extract slots
        logger.info("Detecting intent for user %s (current_intent=%s)", user_id, state.current_intent)
        intent, extracted_slots, missing_slots = self.intent_detector.detect_intent_and_slots(
            user_message, state.conversation_history, state.current_intent, media_context
        )

        # If intervention is active, allow only end_intervention to proceed; otherwise ack and stop.
        if intervention_is_active and intent != "end_intervention":
            response = self.response_formatter.format_response("", "intervention_active")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response

        if intent == "end_intervention":
            # Return automation to normal flow for today
            state.intervention_active = False
            state.intervention_trigger = None
            state.intervention_reason = None
            self.conversation_manager._save_conversation_state(state)
            response = "Okay — I’m back. How can I help?"
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response

        if intent == "request_intervention":
            self._activate_intervention(
                user_id=user_id,
                trigger="explicit_user_request",
                reason=(extracted_slots or {}).get("reason") or user_message or "",
            )
            response = self.response_formatter.format_response("", "intervention_created")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response
        # If the model explicitly reported it cannot process the image, ask the user
        if intent == "cannot_process_image":
            logger.info("Model cannot process image for user %s; asking for description", user_id)
            response = self.response_formatter.format_response("", "ask_for_image_description")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response
        
        # If the intent is not clear due to low confidence, return appropriate response
        if intent == "intent_not_clear":
            logger.info("Intent not clear for user %s; activating intervention", user_id)
            self._activate_intervention(
                user_id=user_id,
                trigger="intent_not_clear",
                reason=user_message or "intent not clear",
            )
            response = self.response_formatter.format_response("", "intervention_created")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response

        # Unknown intent should also request intervention (handover).
        from core.nlu.config import INTENTS
        if intent == "unknown" or intent not in INTENTS:
            logger.info("Unknown intent for user %s (intent=%s); activating intervention", user_id, intent)
            self._activate_intervention(
                user_id=user_id,
                trigger="unknown_intent",
                reason=user_message or "unknown intent",
                metadata={"intent": intent},
            )
            response = self.response_formatter.format_response("", "intervention_created")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return response
        
        logger.info("Detected intent=%s missing=%s", intent, missing_slots)

        merchant_id, _ = self._parse_merchant_scoped_user_id(user_id)
        if merchant_id:
            conversational_only = set(INTENT_CATEGORIES.get("conversational", []))
            if intent not in conversational_only:
                logger.info(
                    "Customer session %s: overriding admin intent '%s' with business_conversation",
                    user_id,
                    intent,
                )
                intent = "business_conversation"
                missing_slots = []
                state.current_intent = ""
                state.collected_slots = {}

        # Validate and merge slots
        validated_slots = self.slot_manager.validate_slots(intent, extracted_slots)

        previous_intent = (state.current_intent or "").strip()
        state.collected_slots.update(validated_slots)
        self._apply_create_order_slot_hygiene(
            previous_intent=previous_intent,
            intent=intent,
            validated_slots=validated_slots,
            collected_slots=state.collected_slots,
            user_message=user_message or "",
        )

        product_image_intents = set(INTENT_CATEGORIES.get("product_management", [])) & {
            "add_product",
            "update_product",
        }
        if intent in product_image_intents and media_context:
            photo_url = self._upload_product_photo_from_media(user_id, media_context)
            if photo_url:
                photos = state.collected_slots.setdefault("photos", [])
                if photo_url not in photos:
                    photos.append(photo_url)
                state.collected_slots["photo"] = photo_url

        state.current_intent = intent

        # CHECK SUBSCRIPTION STATUS EARLY
        # print (f"User Subscription Status: {user_subscription_status}")
        # if not user_subscription_status and intent != "create_new_account":
        #     # User needs subscription but isn't trying to create account
        #     response = self.response_formatter.format_response(
        #         "subscription_required",
        #         "need_subscription",
        #         current_intent=intent  # Pass the original intent for context
        #     )
        #     self.conversation_manager.update_conversation_history(user_id, "assistant", response)
        #     return response

        # Check if user wants to cancel during slot collection
        if state.current_intent and user_message:
            user_msg_lower = user_message.lower().strip()
            cancellation_keywords = ["cancel", "stop", "abort", "never mind", "nevermind", "quit"]

            if any(keyword in user_msg_lower for keyword in cancellation_keywords):
                logger.info(f"[CANCELLATION] User {user_id} cancelled {state.current_intent} during slot collection")
                response = self.response_formatter.format_response(state.current_intent, "payment_cancelled")

                # Clear all transaction state
                state.current_intent = ""
                state.collected_slots = {}
                state.pending_payment_dto = {}
                state.waiting_for_payment_confirmation = False
                self.conversation_manager._save_conversation_state(state)

                self.conversation_manager.update_conversation_history(user_id, "assistant", response)
                return response

        # Check for missing required slots
        current_missing = self.slot_manager.get_missing_slots(intent, state.collected_slots)

        # For customer edits, guide the user through a strict field-selection flow.
        if intent == "update_customer":
            if not state.collected_slots.get("update_field"):
                if state.collected_slots.get("new_customer_name"):
                    state.collected_slots["update_field"] = "name"
                elif state.collected_slots.get("customer_number"):
                    state.collected_slots["update_field"] = "number"
                elif state.collected_slots.get("bank_code"):
                    state.collected_slots["update_field"] = "bank_code"

            update_field = (state.collected_slots.get("update_field") or "").lower().strip()
            field_map = {
                "name": "new_customer_name",
                "rename": "new_customer_name",
                "new name": "new_customer_name",
                "number": "customer_number",
                "phone": "customer_number",
                "phone number": "customer_number",
                "mobile": "customer_number",
            }
            target_slot = field_map.get(update_field)
            if update_field and not target_slot:
                current_missing = ["update_field"]
            elif target_slot and not state.collected_slots.get(target_slot):
                current_missing = [target_slot]

        if current_missing or (len(state.collected_slots) == 1 and 'amount' in state.collected_slots):
            prompt = self.slot_manager.generate_slot_prompt(intent, current_missing)
            response = self.response_formatter.format_response(
                intent, "missing_slots", prompt=prompt
            )

        else:
            # All slots collected, execute action directly
            slots_to_execute = state.collected_slots.copy()
            handler_outcome = self._execute_action(
                user_id, intent, slots_to_execute, user_message, state.conversation_history
            )
            response = self._terminal_listener_apply(user_id, handler_outcome)
        
        # Add assistant response to history
        self.conversation_manager.update_conversation_history(user_id, "assistant", response)

        # Clear collected slots if action was executed
        if not current_missing:
            self.conversation_manager.clear_collected_slots(user_id)
        
        return response
    
    def _handle_pin_verification(self, user_id: str, pin_input: str) -> str:
        """Handle PIN verification for pending actions"""
        state = self.conversation_manager.get_conversation_state(user_id)

        # Validate pending action exists
        if not state.pending_action or "intent" not in state.pending_action or "slots" not in state.pending_action:
            error_response = self.response_formatter.format_response("", "error", message="No pending action found. Please start over.")
            self.conversation_manager.update_conversation_history(user_id, "assistant", error_response)
            self.conversation_manager.reset_conversation_state(user_id)
            return error_response

        if self.security_manager.verify_pin(user_id, pin_input):
            # PIN verified, execute action
            pending_intent = state.pending_action["intent"]
            pending_slots = state.pending_action["slots"]

            print(f"PIN verified for user {user_id}. Executing pending action: intent={pending_intent}, slots={pending_slots}")

            outcome = self._execute_action(
                user_id,
                pending_intent,
                pending_slots,
            )
            response = self._terminal_listener_apply(user_id, outcome)
            state.waiting_for_pin = False
            state.pending_action = {}
            state.collected_slots = {}
            self.conversation_manager._save_conversation_state(state)
        else:
            # Invalid PIN
            response = self.response_formatter.format_response("", "invalid_pin")
            # Keep waiting for PIN

        self.conversation_manager.update_conversation_history(user_id, "assistant", response)
        return response

    def _handle_payment_confirmation(self, user_id: str, user_response: str, media_context: Optional[Dict[str, Any]] = None) -> str:
        """
        Handle user's yes/no response for payment confirmation.
        Supports text, audio, and image inputs for confirmation.
        
        Args:
            user_id: User identifier
            user_response: Text response from user
            media_context: Optional dict with 'audio_bytes', 'image_base64', 'image_url', or other media
            
        Returns:
            Response string to send to user
        """
        state = self.conversation_manager.get_conversation_state(user_id)

        # Check if pending payment exists
        if not state.pending_payment_dto:
            error_response = self.response_formatter.format_response("", "error", message="No pending payment found. Please start over.")
            self.conversation_manager.update_conversation_history(user_id, "assistant", error_response)
            state.waiting_for_payment_confirmation = False
            self.conversation_manager._save_conversation_state(state)
            return error_response

        # Extract confirmation from media if provided
        extracted_confirmation = self._extract_confirmation_from_media(user_id, media_context)
        if extracted_confirmation:
            user_response = extracted_confirmation
            logger.info(f"[PAYMENT_CONFIRMATION] Extracted confirmation from media: {extracted_confirmation}")

        # Check user's response (yes/no/confirm/proceed/etc.)
        user_response_lower = (user_response or "").lower().strip()
        confirmation_keywords = ["yes", "y", "confirm", "ok", "okay", "proceed", "go ahead"]
        rejection_keywords = ["no", "n", "cancel", "don't", "dont", "stop"]

        if any(keyword in user_response_lower for keyword in confirmation_keywords):
            # User confirmed payment
            logger.info(f"[PAYMENT_CONFIRMATION] User {user_id} confirmed payment (response: {user_response_lower})")
            intent = state.current_intent
            # Use slots stored in pending_payment_dto (includes receiver_name and providers)
            slots = state.pending_payment_dto.get("slots", state.collected_slots)

            # Execute the payment with confirmed slots
            outcome = self._execute_action(user_id, intent, slots, user_response, state.conversation_history)
            response = self._terminal_listener_apply(user_id, outcome)

            # Clear confirmation state and collected slots
            state.waiting_for_payment_confirmation = False
            state.pending_payment_dto = {}
            state.collected_slots = {}
            self.conversation_manager._save_conversation_state(state)

        elif any(keyword in user_response_lower for keyword in rejection_keywords):
            # User rejected payment
            logger.info(f"[PAYMENT_CONFIRMATION] User {user_id} rejected payment (response: {user_response_lower})")
            response = self.response_formatter.format_response(state.current_intent, "payment_cancelled")

            # Clear confirmation state
            state.waiting_for_payment_confirmation = False
            state.pending_payment_dto = {}
            state.current_intent = ""
            state.collected_slots = {}
            self.conversation_manager._save_conversation_state(state)

        else:
            # Unclear response, ask again
            logger.info(f"[PAYMENT_CONFIRMATION] User {user_id} gave unclear response: {user_response}")
            response = self.response_formatter.format_response(state.current_intent, "confirm_again",
                                                               message="I didn't understand. Please reply 'yes' to confirm or 'no' to cancel.")

        self.conversation_manager.update_conversation_history(user_id, "assistant", response)
        return response

    def _handle_expense_date_selection(self, user_id: str, user_response: str) -> str:
        """
        Handle user's date selection for expense tracking.
        
        User can select multiple dates by entering numbers separated by spaces or commas.
        Examples: "1 2 3" or "1, 2, 3" or "1,2,3"
        
        Args:
            user_id: User identifier
            user_response: User's selection input (e.g., "1 2 3")
            
        Returns:
            Response string to send to user
        """
        state = self.conversation_manager.get_conversation_state(user_id)
        
        # Check if expense dates are available
        if not state.pending_expense_dates:
            error_response = self.response_formatter.format_response("", "error", message="No date options available. Please start over.")
            self.conversation_manager.update_conversation_history(user_id, "assistant", error_response)
            state.waiting_for_expense_date_selection = False
            self.conversation_manager._save_conversation_state(state)
            return error_response
        
        # Convert stored date dicts back to DateOption objects
        date_options = [
            DateOption(
                number=opt["number"],
                label=opt["label"],
                start_date=datetime.fromisoformat(opt["start_date"]),
                end_date=datetime.fromisoformat(opt["end_date"])
            )
            for opt in state.pending_expense_dates
        ]
        
        # Parse user selections
        selected_options, errors = self.date_selection_manager.parse_selections(user_response, date_options)
        
        if errors:
            logger.info(f"[EXPENSE_SELECTION] User {user_id} provided invalid selections. Errors: {errors}")
            error_msg = "\n".join(errors)
            error_response = self.response_formatter.format_response(
                "expense_report", 
                "error", 
                message=f"I couldn't understand your selection:\n{error_msg}\n\n{self.date_selection_manager.generate_menu_text(date_options)}"
            )
            self.conversation_manager.update_conversation_history(user_id, "assistant", error_response)
            return error_response
        
        # Get the merged date range
        start_date, end_date = self.date_selection_manager.merge_date_ranges(selected_options)
        summary = self.date_selection_manager.format_selected_dates_summary(selected_options)
        
        logger.info(
            f"[EXPENSE_SELECTION] User {user_id} selected dates. "
            f"Summary: {summary}. Date range: {start_date.date()} to {end_date.date()}"
        )
        
        # Update slots with the selected date range
        state.collected_slots["time_period_start"] = start_date.isoformat()
        state.collected_slots["time_period_end"] = end_date.isoformat()
        state.collected_slots["time_period"] = summary
        
        # Get user data for context
        user_data = self._get_user_data(user_id)
        
        # Process expense report with the selected dates
        response = self.intent_processor.process_expense_report_intent(
            intent="expense_report",
            user_message=f"Show my expenses for {summary}",
            conversation_history=state.conversation_history,
            slots=state.collected_slots,
            user_data=user_data
        )
        expense_outcome = IntentHandlerResult(response, 200)
        response = self._terminal_listener_apply(user_id, expense_outcome)
        
        # Clear expense selection state
        state.waiting_for_expense_date_selection = False
        state.pending_expense_dates = []
        state.collected_slots = {}
        self.conversation_manager._save_conversation_state(state)
        
        self.conversation_manager.update_conversation_history(user_id, "assistant", response)
        return response

    def _extract_confirmation_from_media(self, user_id: str, media_context: Optional[Dict[str, Any]]) -> Optional[str]:
        """
        Extract confirmation (yes/no) from media inputs (audio or image).
        
        Args:
            user_id: User identifier
            media_context: Dictionary with 'audio_bytes', 'image_base64', 'image_url', etc.
            
        Returns:
            Confirmation string ('yes' or 'no') or None if media processing fails
        """
        if not media_context:
            return None

        # Priority 1: Process audio if available
        if media_context.get("audio_bytes"):
            try:
                logger.info(f"[MEDIA_CONFIRMATION] Transcribing audio for user {user_id}")
                audio_filename = media_context.get("audio_filename", "audio.mp3")
                audio_bytes = media_context.get("audio_bytes")
                
                transcription = self.intent_detector.llm_client.transcribe_audio_from_bytes(
                    audio_bytes,
                    filename=audio_filename
                )
                
                if transcription:
                    logger.info(f"[MEDIA_CONFIRMATION] Audio transcribed: {transcription[:100]}")
                    return transcription
                else:
                    logger.warning(f"[MEDIA_CONFIRMATION] Audio transcription returned empty for user {user_id}")
                    
            except Exception as e:
                logger.error(f"[MEDIA_CONFIRMATION] Error transcribing audio for user {user_id}: {str(e)}", exc_info=True)

        # Priority 2: Process image if available
        if media_context.get("image_base64") or media_context.get("image_url"):
            try:
                logger.info(f"[MEDIA_CONFIRMATION] Interpreting image for user {user_id}")
                
                # Build prompt for image interpretation
                system_prompt = (
                    "You are a payment confirmation classifier. Analyze the provided image and determine "
                    "if it represents a CONFIRMATION or REJECTION of a payment transaction.\n"
                    "Respond with ONLY one word: 'yes' for confirmation or 'no' for rejection.\n"
                    "Examples of yes: thumbs up, checkmark, tick, nod, positive gesture, 'yes' text, confirmation sign\n"
                    "Examples of no: thumbs down, cross, X mark, shake head, negative gesture, 'no' text, rejection sign"
                )
                
                user_msg = (
                    "Based on this image, should I confirm and process the pending payment? "
                    "Respond with only 'yes' or 'no'."
                )
                
                image_base64 = media_context.get("image_base64")
                image_url = media_context.get("image_url")
                image_mime = media_context.get("image_mime_type") or media_context.get("mime_type") or "image/jpeg"
                
                logger.debug(f"[MEDIA_CONFIRMATION] Image parameters - mime_type: {image_mime}, has_base64: {bool(image_base64)}, has_url: {bool(image_url)}")
                
                img_response = self.intent_detector.llm_client.chat_completion(
                    system_prompt=system_prompt,
                    user_message=user_msg,
                    conversation_history=None,
                    temperature=0.0,  # Deterministic response
                    max_tokens=10,
                    image_url=image_url,
                    image_base64=image_base64,
                    image_media_type=image_mime,
                )
                
                if img_response:
                    logger.info(f"[MEDIA_CONFIRMATION] Image interpretation result: {img_response}")
                    return img_response.strip()
                else:
                    logger.warning(f"[MEDIA_CONFIRMATION] Image interpretation returned empty for user {user_id}")
                    
            except Exception as e:
                logger.error(f"[MEDIA_CONFIRMATION] Error interpreting image for user {user_id}: {str(e)}", exc_info=True)

        logger.debug(f"[MEDIA_CONFIRMATION] No media confirmation could be extracted for user {user_id}")
        return None

    def _execute_action(self, user_id: str, intent: str, slots: Dict, user_message: str = "", conversation_history: List[Dict] = None) -> IntentHandlerResult:
        """Execute the actual financial action through payment service."""
        try:
            # Payment intents that require Orchard API
            payment_intents = ["buy_airtime", "send_money", "pay_bill", "get_loan"]

            if intent in payment_intents:
                return self._process_payment_intent(user_id, intent, slots, user_message)
            else:
                return self._process_non_payment_intent(user_id, intent, user_message, conversation_history, slots)

        except Exception as e:
            import traceback
            print(f"[EXECUTE_ACTION] ERROR: {e}")
            traceback.print_exc()
            # Escalate to human intervention on execution errors.
            self._activate_intervention(
                user_id=user_id,
                trigger="execution_error",
                reason=str(e),
                metadata={"intent": intent},
            )
            return IntentHandlerResult(self.response_formatter.format_response("", "intervention_created"), None)

    def _process_payment_intent(self, user_id: str, intent: str, slots: Dict, user_message: str = "") -> IntentHandlerResult:
        """Process payment intents through PaymentService"""

        db = SessionLocal()
        try:
            # Map network string to Network enum (per Orchard API spec)
            network_map = {
                "MTN": Network.MTN,
                "Vodafone": Network.VOD,
                "VOD": Network.VOD,
                "AirtelTigo": Network.AIR,
                "AIR": Network.AIR,
                "Mastercard": Network.MAS,
                "MAS": Network.MAS,
                "VISA": Network.VIS,
                "VIS": Network.VIS,
                "Bank": Network.BNK,
                "BNK": Network.BNK
            }

            print(f"[PAYMENT_INTENT] Creating PaymentDto for intent: {intent}")

            # Resolve customer for buy_airtime and send_money if customer_name slot exists
            if intent == "buy_airtime" or intent == "send_money":
                customer_name = slots.get('customer_name')
                needs_lookup = (not slots.get('phone_number')) if intent == "buy_airtime" else (not slots.get('recipient'))
                if customer_name and needs_lookup:
                    logger.info(f"[BENEFICIARY_RESOLUTION] Resolving customer for {intent}: {customer_name}")
                    customer_info = self._resolve_customer(user_id, customer_name, db)
                    if customer_info:
                        # Update slots with resolved customer information
                        slots['phone_number'] = customer_info['customer_number']
                        slots['network'] = customer_info['network']
                        slots['customer_matched'] = customer_info['name']
                        slots['customer_id'] = customer_info['id']
                        logger.info(f"[BENEFICIARY_RESOLUTION] Customer resolved: {customer_info['name']} → {customer_info['customer_number']}")
                    else:
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "error", message=f"Customer '{customer_name}' not found in your saved contacts. Please provide the phone number directly or save this customer first."),
                            None,
                        )

            # Create PaymentDto based on intent
            if intent == "buy_airtime":
                sender_name = "User"
                try:
                    sender_user = db.query(User).filter(
                        or_(
                            User.phone == user_id,
                            User.whatsapp_phone == user_id
                        )
                    ).first()
                    if sender_user:
                        sender_name = f"{sender_user.first_name} {sender_user.last_name}".strip()
                except Exception as e:
                    logger.warning(f"Could not fetch user name for {user_id}: {e}")
                    
                payment_dto = PaymentDto(
                    senderPhone=user_id,  # User initiating the payment
                    receiverPhone=slots.get('phone_number', user_id),  # Use extracted phone number (supports buying airtime for others)
                    network=network_map.get(slots.get('network', 'MTN'), Network.MTN),
                    paymentMethod=PaymentMethod.MOBILE_MONEY,
                    serviceName="Airtime Top-Up",
                    reference="Airtime Purchase",
                    amountPaid=Decimal(slots.get('amount', '0')),
                    transactionId=str(UniqueIdGenerator.generate()),
                    customerName=slots.get('recipient_name', 'Unknown'),
                    senderName=sender_name,  # Actual user name from database
                    receiverName=slots.get('receiver_name'),  # Verified account holder name from account inquiry
                    senderProvider=slots.get('sender_provider'),  # Provider for sender
                    receiverProvider=slots.get('receiver_provider'),  # Provider for receiver
                    customerId=slots.get('customer_id')
                )

            elif intent == "send_money":
                # Get sender's actual name from database
                from core.user.model.User import User
                sender_name = "User"
                try:
                    sender_user = db.query(User).filter(
                        or_(
                            User.phone == user_id,
                            User.whatsapp_phone == user_id
                        )
                    ).first()
                    if sender_user:
                        sender_name = f"{sender_user.first_name} {sender_user.last_name}".strip()
                except Exception as e:
                    logger.warning(f"Could not fetch user name for {user_id}: {e}")

                payment_dto = PaymentDto(
                    senderPhone=user_id,  # User initiating the payment
                    receiverPhone=slots.get('recipient'),  # Recipient gets the money
                    network=network_map.get(slots.get('network', 'MTN'), Network.MTN),
                    paymentMethod=PaymentMethod.MOBILE_MONEY,
                    customerName=slots.get('recipient_name', 'Unknown'),
                    senderName=sender_name,  # Actual user name from database
                    receiverName=slots.get('receiver_name'),  # Verified account holder name from account inquiry
                    senderProvider=slots.get('sender_provider'),  # Provider for sender
                    receiverProvider=slots.get('receiver_provider'),  # Provider for receiver
                    serviceName=f"Money Transfer to {slots.get('recipient')}",
                    reference=slots.get('reference'),
                    customerId=slots.get('customer_id'),
                    amountPaid=Decimal(slots.get('amount', '0')),
                    transactionId=str(UniqueIdGenerator.generate())
                )

            elif intent == "pay_bill":
                # For bill payment: senderPhone is user, receiverPhone is the smart card/account number
                bill_type = slots.get('bill_type', '')
                account_number = slots.get('account_number', '')

                # Map bill_type to utility network codes (GOT, DST, ECG, GHW, etc.)
                # bill_type examples: GoTV, DStv, ECG, Ghana Water, Surfline, etc.
                bill_network_map = {
                    'gotv': Network.GOT,
                    'dstv': Network.DST,
                    'ecg': Network.ECG,
                    'ghana water': Network.GHW,
                    'water': Network.GHW,
                    'surfline': Network.SFL,
                    'telesol': Network.TLS,
                    'startimes': Network.STT,
                    'box office': Network.BXO,
                }

                # Telco bill networks (don't require external billers inquiry)
                # These are predefined in the system and don't need external biller lookup
                telco_networks = {Network.GOT, Network.DST, Network.STT}

                # Map non-telco bill types to external biller IDs (for ABS payments)
                # These are obtained from the /ext-billers INF endpoint
                biller_id_map = {
                    'ecg': '0E8440AA1',  # Electricity Company of Ghana
                    'ghana water': 'GHW_ID',  # Ghana Water Company (placeholder - get from INF)
                    'water': 'GHW_ID',
                    'surfline': 'SFL_ID',  # Surfline (placeholder)
                    'telesol': 'TLS_ID',  # Telesol (placeholder)
                    'box office': 'BXO_ID',  # Box Office (placeholder)
                    'gotv': 'F804DBCF',  # GoTV (if ABS instead of telco)
                }

                # Try to match bill_type to network, default to GOT if unknown
                selected_network = Network.GOT
                for key, network in bill_network_map.items():
                    if key in bill_type.lower():
                        selected_network = network
                        break

                # For ABS bills after confirmation, retrieve biller_id from pending_payment_dto
                ext_biller_ref_id = None
                amount_to_pay = slots.get('amount', '0')
                state_temp = self.conversation_manager.get_conversation_state(user_id)
                if state_temp.pending_payment_dto:
                    ext_biller_ref_id = state_temp.pending_payment_dto.get('biller_id')
                    # Use invoice amount if available (for fixed bills)
                    invoice_amount = state_temp.pending_payment_dto.get('invoice_amount')
                    if invoice_amount:
                        amount_to_pay = invoice_amount

                payment_dto = PaymentDto(
                    senderPhone=user_id,  # User initiating the payment (paying the bill)
                    receiverPhone=account_number,  # Smart card/account number where bill is paid
                    network=selected_network,  # Utility provider (GoTV, DStv, ECG, etc.)
                    paymentMethod=PaymentMethod.MOBILE_MONEY,
                    serviceName=f"Bill Payment: {bill_type}",
                    amountPaid=Decimal(amount_to_pay),
                    transactionId=str(UniqueIdGenerator.generate()),
                    extBillerRefId=ext_biller_ref_id  # Set biller ID for ABS bills
                )

            elif intent == "get_loan":
                payment_dto = PaymentDto(
                    senderPhone=user_id,  # User receiving payout (merchant → user)
                    receiverPhone=user_id,  # Payout to user's account
                    network=network_map.get(slots.get('network', 'MTN'), Network.MTN),
                    paymentMethod=PaymentMethod.MOBILE_MONEY,
                    serviceName="Loan Disbursement",
                    amountPaid=Decimal(slots.get('loan_amount', '0')),
                    transactionId=str(UniqueIdGenerator.generate())
                )
            else:
                return IntentHandlerResult(
                    self.response_formatter.format_response(intent, "error", message=f"Unknown payment intent: {intent}"),
                    None,
                )

            print(f"[PAYMENT_INTENT] PaymentDto created successfully")

            # For pay_bill with non-telco (ABS), perform invoice and biller inquiry
            state = self.conversation_manager.get_conversation_state(user_id)
            if intent == "pay_bill" and selected_network not in telco_networks and not state.pending_payment_dto:
                logger.info(f"[BILL_INQUIRY] Performing invoice and biller inquiry for non-telco bill: {bill_type}")
                try:
                    payment_service = PaymentService(db)

                    # Get biller ID from mapping
                    biller_id = None
                    for key, bid in biller_id_map.items():
                        if key in bill_type.lower():
                            biller_id = bid
                            break

                    if not biller_id:
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "error", message=f"Unknown biller type: {bill_type}"),
                            None,
                        )

                    # Step 1: Call INV inquiry to get customer invoice details
                    logger.info(f"[BILL_INQUIRY] Calling INV for biller_id={biller_id}, customer_ref={account_number}")
                    invoice_response = payment_service.payment_gateway_client.external_biller_invoice_inquiry(
                        ext_biller_ref_id=biller_id,
                        ext_biller_pan=account_number,
                        ext_biller_ref_type=bill_type,
                        network="ABS",
                        operation="INV"
                    )

                    if invoice_response.status_code == 200:
                        invoice_data = invoice_response.json()
                        logger.info(f"[BILL_INQUIRY_INV_SUCCESS] Response: {invoice_data}")

                        # Extract invoice details
                        invoice_details = invoice_data.get("details", [{}])[0] if invoice_data.get("details") else {}
                        customer_name = invoice_details.get("invoiceName", "the customer")
                        invoice_amount = invoice_details.get("invoiceAmount")  # Can be null for flexible payments
                        invoice_id = invoice_details.get("invoiceId", account_number)

                        # Step 2: Call INF inquiry to get biller payment rules
                        logger.info(f"[BILL_INQUIRY] Calling INF for biller_id={biller_id}")
                        try:
                            biller_info_response = payment_service.payment_gateway_client.external_billers_inquiry(
                                customer_number=account_number,
                                network="ABS",
                                operation="INF"
                            )
                        except Exception as e:
                            logger.warning(f"[BILL_INQUIRY_INF_WARNING] Could not fetch biller rules: {str(e)}, continuing with invoice details only")
                            biller_info_response = None

                        biller_rules = {}
                        if biller_info_response and biller_info_response.status_code == 200:
                            billers_data = biller_info_response.json()
                            logger.info(f"[BILL_INQUIRY_INF_SUCCESS] Response received")

                            # Find matching biller in the list
                            for biller in billers_data.get("data", []):
                                if biller.get("billerId") == biller_id:
                                    biller_rules = {
                                        "billerName": biller.get("billerName"),
                                        "billerCategory": biller.get("billerCategory"),
                                        "paymentFlag": biller.get("paymentFlag"),  # PayPart or PayFull
                                        "minAmount": biller.get("minAmount"),
                                        "maxAmount": biller.get("maxAmount")
                                    }
                                    break

                        # Create confirmation message with invoice and biller details
                        if invoice_amount:
                            confirmation_msg = f"Bill for {customer_name}:\n"
                            confirmation_msg += f"Amount Due: GHS {invoice_amount}\n"
                            if biller_rules:
                                confirmation_msg += f"Min Payment: GHS {biller_rules.get('minAmount', 'N/A')}, "
                                confirmation_msg += f"Max: GHS {biller_rules.get('maxAmount', 'N/A')}\n"
                            confirmation_msg += f"Please reply 'yes' to confirm or 'no' to cancel."
                        else:
                            # Flexible payment - user can pay any amount
                            confirmation_msg = f"Bill for {customer_name} (Flexible Payment):\n"
                            if biller_rules:
                                confirmation_msg += f"Payment Range: GHS {biller_rules.get('minAmount', '0')} - GHS {biller_rules.get('maxAmount', 'unlimited')}\n"
                            confirmation_msg += f"Please reply 'yes' to confirm or 'no' to cancel."

                        # Store payment info and set waiting for confirmation
                        state.current_intent = intent
                        state.collected_slots = slots
                        state.waiting_for_payment_confirmation = True
                        state.pending_payment_dto = {
                            "bill_type": bill_type,
                            "customer_name": customer_name,
                            "customer_ref": account_number,
                            "biller_id": biller_id,
                            "invoice_amount": invoice_amount,
                            "invoice_id": invoice_id,
                            "biller_rules": biller_rules,
                            "slots": slots
                        }
                        self.conversation_manager._save_conversation_state(state)

                        logger.info(f"[BILL_INQUIRY] Waiting for payment confirmation from user {user_id}")
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "payment_confirmation", message=confirmation_msg),
                            None,
                        )

                    else:
                        try:
                            response_data = invoice_response.json()
                            error_msg = response_data.get("resp_desc", "Invoice inquiry failed") if isinstance(response_data, dict) else str(response_data)
                        except:
                            error_msg = f"API returned status {invoice_response.status_code}: {invoice_response.text[:100]}"
                        logger.error(f"[BILL_INQUIRY_FAILED] Status: {invoice_response.status_code}, Error: {error_msg}")
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "error", message=f"Could not retrieve bill details: {error_msg}"),
                            None,
                        )

                except Exception as e:
                    logger.error(f"[BILL_INQUIRY_ERROR] Error during bill inquiry: {str(e)}", exc_info=True)
                    return IntentHandlerResult(
                        self.response_formatter.format_response(intent, "error", message=f"Error retrieving bill details: {str(e)}"),
                        None,
                    )

            # For send_money, perform account inquiry and wait for confirmation (only if not already done)
            if intent == "send_money" and not state.pending_payment_dto:
                logger.info(f"[ACCOUNT_INQUIRY] Performing account inquiry for send_money")
                
                # First, resolve customer if customer_name slot exists
                customer_name = slots.get('customer_name')
                if customer_name and not slots.get('recipient'):
                    logger.info(f"[BENEFICIARY_RESOLUTION] Resolving customer: {customer_name}")
                    customer_info = self._resolve_customer(user_id, customer_name, db)
                    if customer_info:
                        # Update slots with resolved customer information
                        slots['recipient'] = customer_info['customer_number']
                        slots['network'] = customer_info['network']
                        slots['customer_matched'] = customer_info['name']
                        slots['customer_id'] = customer_info['id']
                        logger.info(f"[BENEFICIARY_RESOLUTION] Customer resolved: {customer_info['name']} → {customer_info['customer_number']}")
                    else:
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "error", message=f"Customer '{customer_name}' not found in your saved contacts. Please provide the phone number directly or save this customer first."),
                            None,
                        )

                # Fallback: if no recipient yet but we have a reference, try regex matching on raw message
                if not slots.get('recipient') and slots.get('reference'):
                    regex_match = self._resolve_customer_from_message(user_id, user_message, db)
                    if regex_match:
                        slots['recipient'] = regex_match['customer_number']
                        slots['network'] = slots.get('network') or regex_match['network']
                        slots['customer_matched'] = regex_match['name']
                        slots['customer_id'] = regex_match['id']
                        slots['customer_name'] = slots.get('customer_name') or regex_match['name']
                    else:
                        return IntentHandlerResult(
                            self.response_formatter.format_response(
                                intent,
                                "missing_slots",
                                prompt="Please provide the recipient's phone number or a saved customer name.",
                            ),
                            None,
                        )
                
                try:
                    payment_service = PaymentService(db)
                    recipient_phone = slots.get('recipient')
                    slot_network = slots.get('network')
                    detected_network, _ = NetworkDetector.detect_network_from_phone(recipient_phone or "")
                    recipient_network = network_map.get(slot_network) if slot_network else None
                    if not recipient_network and detected_network:
                        recipient_network = network_map.get(detected_network, Network.MTN)
                    if not recipient_network:
                        recipient_network = Network.MTN

                    # Call account inquiry
                    inquiry_response = payment_service.payment_gateway_client.account_inquiry(
                        customer_number=recipient_phone,
                        network=recipient_network.value
                    )

                    if inquiry_response.status_code == 200:
                        inquiry_data = inquiry_response.json()
                        logger.info(f"[ACCOUNT_INQUIRY_SUCCESS] Response: {inquiry_data}")

                        # Extract account holder name from response
                        account_name = inquiry_data.get("account_name") or inquiry_data.get("name") or "the recipient"
                        amount = slots.get('amount')

                        # Detect sender's network from sender's phone
                        from utilities.provider_mapper import ProviderMapper

                        sender_network_tuple = NetworkDetector.detect_network_from_phone(user_id)
                        sender_network_str = sender_network_tuple[0] if isinstance(sender_network_tuple, tuple) else sender_network_tuple
                        sender_network = network_map.get(sender_network_str, Network.MTN)

                        # Update PaymentDto with sender and receiver information
                        payment_dto.receiverName = account_name
                        payment_dto.senderProvider = ProviderMapper.get_provider(sender_network)
                        payment_dto.receiverProvider = ProviderMapper.get_provider(recipient_network)

                        # Add receiver_name to slots for later use
                        slots_with_receiver = dict(slots)
                        slots_with_receiver['receiver_name'] = account_name
                        slots_with_receiver['sender_provider'] = ProviderMapper.get_provider(sender_network)
                        slots_with_receiver['receiver_provider'] = ProviderMapper.get_provider(recipient_network)

                        # Create confirmation message with provider information
                        receiver_provider = ProviderMapper.get_provider(recipient_network)
                        reference = slots.get('reference')
                        reference_line = f"\nReference: {reference}" if reference else ""
                        confirmation_msg = (
                            f"Are you sure you want to send GHS {amount} to {recipient_phone} ({account_name}) on {receiver_provider}?"
                            f"{reference_line}\nPlease reply 'yes' to confirm or 'no' to cancel."
                        )

                        # Store payment info and set waiting for confirmation
                        state.current_intent = intent
                        state.collected_slots = slots_with_receiver
                        state.waiting_for_payment_confirmation = True
                        state.pending_payment_dto = {
                            "account_name": account_name,
                            "recipient_phone": recipient_phone,
                            "amount": amount,
                            "slots": slots_with_receiver,  # Store all slots with receiver_name for later use
                            "sender_provider": ProviderMapper.get_provider(sender_network),
                            "receiver_provider": ProviderMapper.get_provider(recipient_network)
                        }
                        self.conversation_manager._save_conversation_state(state)

                        logger.info(f"[ACCOUNT_INQUIRY] Waiting for payment confirmation from user {user_id}")
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "payment_confirmation", message=confirmation_msg),
                            None,
                        )

                    else:
                        error_msg = inquiry_response.json().get("resp_desc", "Account inquiry failed")
                        logger.error(f"[ACCOUNT_INQUIRY_FAILED] Status: {inquiry_response.status_code}, Error: {error_msg}")
                        return IntentHandlerResult(
                            self.response_formatter.format_response(intent, "error", message=f"Could not verify recipient account: {error_msg}"),
                            None,
                        )

                except Exception as e:
                    logger.error(f"[ACCOUNT_INQUIRY_ERROR] Error during account inquiry: {str(e)}", exc_info=True)
                    # Fall back to regular processing if inquiry fails
                    logger.info(f"[ACCOUNT_INQUIRY_FALLBACK] Falling back to direct payment processing")

            print(f"[PAYMENT_INTENT] Calling PaymentService.make_payment() with intent={intent}")

            # Process payment through PaymentService
            payment_service = PaymentService(db)

            result = payment_service.make_payment(payment_dto, intent)

            print(f"[PAYMENT_INTENT] Payment result: status={result.status}, response_code={result.responseCode}, transaction_id={result.transactionId}")

            # Create history record
            history_service = HistoryService(db)

            transaction_mapping = {
                "buy_airtime": ("debit", slots.get('amount')),
                "send_money": ("debit", slots.get('amount')),
                "pay_bill": ("debit", slots.get('amount')),
                "get_loan": ("credit", slots.get('loan_amount'))
            }

            transaction_type, amount = transaction_mapping.get(intent, (None, None))

            if transaction_type:
                history_service.create_history(
                    user_id=user_id,
                    intent=intent,
                    transaction_type=transaction_type,
                    amount=amount,
                    recipient=slots.get('recipient') or slots.get('phone_number'),
                    phone_number=user_id,
                    description=f"{intent.replace('_', ' ').title()} - Transaction ID: {payment_dto.transactionId}",
                    metadata={"slots": slots, "payment_status": result.status}
                )

            # Return response based on payment result
            # NOTE: Receipt generation happens in the callback, not here
            if result.status == PaymentStatus.PENDING:
                message = self._get_processing_message(intent, slots, result)
                return IntentHandlerResult(
                    self.response_formatter.format_response(intent, message_type="processing", message=message),
                    None,
                )
            elif result.status == PaymentStatus.SUCCESS:
                message = self._get_success_message(intent, slots, result)
                
                # Store successful transaction for potential payflow saving
                state = self.conversation_manager.get_conversation_state(user_id)
                state.last_successful_transaction = {
                    "intent": intent,
                    "slots": slots,
                    "transaction_id": result.transactionId,
                    "amount": slots.get('amount') or slots.get('loan_amount'),
                    "timestamp": datetime.now().isoformat()
                }
                self.conversation_manager._save_conversation_state(state)
                
                # Add payflow saving suggestion to the success message
                payflow_suggestion = (
                    "\n\n💾 Would you like to save this as a payment template for quick reuse? "
                    "Just say 'Save as [template name]' (e.g., 'Save as Mom Payment')"
                )
                
                full_message = message + payflow_suggestion
                return IntentHandlerResult(
                    self.response_formatter.format_response(intent, message_type="success", message=full_message),
                    200,
                )
            else:
                error_msg = result.responseDescription or "Payment processing failed"
                return IntentHandlerResult(
                    self.response_formatter.format_response(intent, message_type="error", message=error_msg),
                    None,
                )

        finally:
            db.close()

    def _process_non_payment_intent(self, user_id: str, intent: str, user_message: str, conversation_history: List[Dict], slots: Dict) -> IntentHandlerResult:
        """Process non-payment intents; http_status 200 means fulfilled (terminal success)."""
        conversational_intents = INTENT_CATEGORIES["conversational"]
        financial_tips_intents = INTENT_CATEGORIES["financial_tips"]
        expense_report_intents = INTENT_CATEGORIES["expense_report"]
        customers_intents = INTENT_CATEGORIES["customers"]
        user_management_intents = INTENT_CATEGORIES.get("user_management", [])
        email_intents = INTENT_CATEGORIES.get("email", [])
        video_generation_intents = INTENT_CATEGORIES.get("video_generation", [])
        image_generation_intents = INTENT_CATEGORIES.get("image_generation", [])
        product_management_intents = INTENT_CATEGORIES.get("product_management", [])
        order_management_intents = INTENT_CATEGORIES.get("order_management", [])
        
        logger.info(f"Processing non-payment intent '{intent}' for user {user_id}")

        user_data = self._get_user_data(user_id)

        # Public-site customers chat as ``<merchant_id>:<phone>`` — never run merchant admin flows.
        if user_data and user_data.get("is_customer_session"):
            if intent not in conversational_intents:
                logger.info(
                    "Customer session %s: redirecting admin intent '%s' to business_conversation",
                    user_id,
                    intent,
                )
                intent = "business_conversation"
        
        if intent in conversational_intents:
            msg = self._process_conversational_with_rag(
                user_id=user_id,
                intent=intent,
                user_message=user_message,
                conversation_history=conversation_history,
                slots=slots,
                user_data=user_data,
            )
            return IntentHandlerResult(msg, None)
        elif intent in financial_tips_intents:
            msg = self.intent_processor.process_financial_tips_intent(
                intent,
                user_message, 
                conversation_history, 
                slots,
                user_data
            )
            return IntentHandlerResult(msg, None)
        elif intent in expense_report_intents:
            # Check if time_period was already extracted from the user message
            time_period = slots.get("time_period")
            
            if time_period:
                # User provided a time period (e.g., "show my expenses for today")
                # Convert it to corresponding date options automatically
                logger.info(f"[EXPENSE_REPORT] User {user_id} provided time_period in message: '{time_period}'")
                
                mapped_options = self.date_selection_manager.convert_time_period_to_options(time_period)
                
                if mapped_options:
                    # We have valid date options - process expense report directly
                    logger.info(f"[EXPENSE_REPORT] Mapped time_period to {len(mapped_options)} option(s): {[opt.label for opt in mapped_options]}")
                    
                    # Merge date ranges from the mapped options
                    start_date, end_date = self.date_selection_manager.merge_date_ranges(mapped_options)
                    summary = self.date_selection_manager.format_selected_dates_summary(mapped_options)
                    
                    # Update slots with the determined date range
                    slots["time_period_start"] = start_date.isoformat()
                    slots["time_period_end"] = end_date.isoformat()
                    slots["time_period"] = summary
                    
                    # Process expense report with the extracted dates (skip menu)
                    response = self.intent_processor.process_expense_report_intent(
                        intent="expense_report",
                        user_message=user_message,
                        conversation_history=conversation_history,
                        slots=slots,
                        user_data=user_data
                    )
                    return IntentHandlerResult(response, 200)
                else:
                    # Could not map the time_period - show menu as fallback
                    logger.warning(f"[EXPENSE_REPORT] Could not map time_period '{time_period}', showing menu instead")
            
            # No time_period extracted (or mapping failed) - show date selection menu
            state = self.conversation_manager.get_conversation_state(user_id)
            
            # Generate date options
            date_options = self.date_selection_manager.generate_date_options()
            
            # Store date options in state for later retrieval
            state.pending_expense_dates = [opt.to_dict() for opt in date_options]
            state.waiting_for_expense_date_selection = True
            state.current_intent = intent
            self.conversation_manager._save_conversation_state(state)
            
            # Generate and return the menu
            menu_text = self.date_selection_manager.generate_menu_text(date_options)
            return IntentHandlerResult(menu_text, None)
        elif intent in customers_intents:
            msg, http = self.intent_processor.process_customers_intent(
                intent,
                user_message,
                conversation_history,
                slots,
                user_data
            )
            return IntentHandlerResult(msg, http)
        elif intent in email_intents:
            # Route email intents to EmailTool
            msg = self.intent_processor.process_email_intent(
                intent,
                user_message,
                conversation_history,
                slots,
                user_id=user_id,
                agent_name="email_agent",
                user_data=user_data
            )
            m = (msg or "").strip()
            http = 200 if m.startswith(("✅", "📧")) else None
            return IntentHandlerResult(msg, http)
        elif intent in product_management_intents:
            # Route product management intents
            msg = self.intent_processor.process_product_management_intent(
                intent,
                user_message,
                conversation_history,
                slots,
                user_id=user_id,
                user_data=user_data
            )
            return IntentHandlerResult(msg, 200 if (msg or "").strip().startswith("✅") else None)
        elif intent in order_management_intents:
            # Route order management intents
            msg = self.intent_processor.process_order_management_intent(
                intent,
                user_message,
                conversation_history,
                slots,
                user_id=user_id,
                user_data=user_data,
            )
            return IntentHandlerResult(msg, 200 if (msg or "").strip().startswith("✅") else None)
        elif intent in user_management_intents:
            return self._process_user_management_intent(user_id, intent, slots)
        else:
            # Fallback for unhandled intents
            return IntentHandlerResult(
                self.response_formatter.format_response(intent, "error", message="Intent not supported"),
                None,
            )

    def _resolve_internal_user_id(
        self, user_id: str, user_data: Optional[Dict[str, Any]]
    ) -> str:
        internal_user_id = (user_data or {}).get("db_user_id")
        if internal_user_id:
            return str(internal_user_id)
        try:
            db = SessionLocal()
            user_service = UserService(db)
            user = user_service.get_user_by_phone(user_id)
            internal_user_id = str(user.id) if user else user_id
            db.close()
            return internal_user_id
        except Exception as e:
            logger.warning(f"Could not fetch internal user ID for {user_id}: {e}")
            return user_id

    def _process_conversational_with_rag(
        self,
        *,
        user_id: str,
        intent: str,
        user_message: str,
        conversation_history: List[Dict],
        slots: Dict,
        user_data: Optional[Dict[str, Any]],
    ) -> str:
        """Answer conversational intents via Qdrant retrieval + tenant-scoped LLM."""
        internal_user_id = self._resolve_internal_user_id(user_id, user_data)
        tenant_id = resolve_effective_rag_tenant_id(
            user_data,
            fallback_db_user_id=internal_user_id,
        )

        rag_context = None
        if not self._conversation_rag.enabled():
            logger.warning(
                "[RAG] RAG_SERVICE_URL not configured; conversational reply will lack indexed context"
            )
        elif not tenant_id:
            logger.warning("[RAG] No tenant_id for user %s; skipping vector search", user_id)
        else:
            try:
                hits = self._conversation_rag.search(
                    tenant_id=tenant_id,
                    query=user_message,
                    limit=12,
                )
                rag_context = self._conversation_rag.format_context(hits)
            except Exception as e:
                logger.warning(f"[RAG] search failed for {user_id}: {e}", exc_info=True)

        msg = self.intent_processor.process_conversational_intent(
            intent,
            user_message,
            conversation_history,
            slots,
            user_id=internal_user_id,
            user_data=user_data,
            rag_context=rag_context,
        )

        if tenant_id and self._conversation_rag.enabled():
            try:
                chatter_phone = (
                    (user_data or {}).get("customer_phone")
                    or (user_data or {}).get("user_id")
                    or user_id
                )
                meta: Dict[str, Any] = {
                    "user_phone": chatter_phone,
                    "db_user_id": str(internal_user_id),
                }
                self._conversation_rag.upsert_turns(
                    tenant_id=tenant_id,
                    points=[
                        {"text": user_message, "role": "user", "metadata": meta},
                        {"text": msg, "role": "assistant", "metadata": meta},
                    ],
                )
            except Exception as e:
                logger.warning(f"[RAG] upsert failed for {user_id}: {e}", exc_info=True)

        return msg

    def _route_conversational_intent_to_chatwoot(
        self,
        *,
        user_id: str,
        user_message: str,
        user_data: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """
        If the Autobus user has a provisioned Chatwoot account, forward the message into Chatwoot.
        Returns a response string if routed, else None (meaning: not provisioned, use internal fallback).
        """
        base_url = (os.getenv("CHATWOOT_BASE_URL") or "").strip()
        if not base_url:
            return None

        db_user_id = (user_data or {}).get("db_user_id")
        if not db_user_id:
            return None

        db = SessionLocal()
        try:
            mapping = db.query(ChatwootAccount).filter(ChatwootAccount.user_id == str(db_user_id)).first()
            if not mapping:
                return None

            access_token = decrypt_secret(mapping.chatwoot_user_access_token_encrypted)
            if not access_token:
                return None

            client = ChatwootAccountClient(
                base_url=base_url,
                account_id=int(mapping.chatwoot_account_id),
                user_access_token=access_token,
            )

            inbox_id = client.get_or_create_api_inbox_id(preferred_name="Autobus API")

            # Use stable identifier: Autobus internal user id (db pk)
            contact_identifier = str(db_user_id)
            contact_name = None
            contact_email = (user_data or {}).get("email")
            contact_phone = user_id

            reply_timeout_s = float(os.getenv("CHATWOOT_SYNC_REPLY_TIMEOUT_S", "2.5") or "2.5")
            reply = client.send_and_wait_for_reply(
                inbox_id=inbox_id,
                contact_identifier=contact_identifier,
                contact_name=contact_name,
                contact_email=contact_email,
                contact_phone=contact_phone,
                user_message=user_message,
                reply_timeout_s=reply_timeout_s,
            )

            if reply:
                return reply

            # If no bot/agent replied synchronously, return an ack (message still delivered to Chatwoot).
            return "Got it — I’ve sent this to support. You’ll get a reply shortly."
        finally:
            db.close()
    
    def _process_user_management_intent(self, user_id: str, intent: str, slots: Dict) -> IntentHandlerResult:
        """Process user management intents (update profile, view profile, update username, update phone)"""
        db = SessionLocal()
        try:
            from core.user.service.user_service import UserService
            user_service = UserService(db)
            
            if intent == "update_username":
                new_username = slots.get("new_username")
                
                if not new_username:
                    return IntentHandlerResult(
                        self.response_formatter.format_response(
                            intent, "error", message="No new username provided."
                        ),
                        None,
                    )
                
                update_data = {"fullname": new_username}
                user_service.update_user_details(user_id, update_data)
                
                response = self.response_formatter.format_response(
                    intent, "success", 
                    message=f"Your username has been updated to '{new_username}' successfully! ✅"
                )
                logger.info(f"User {user_id} username updated to {new_username}")
                return IntentHandlerResult(response, 200)
                
            elif intent == "update_phone_number":
                phone_number = slots.get("phone_number")
                
                if not phone_number:
                    return IntentHandlerResult(
                        self.response_formatter.format_response(
                            intent, "error", message="No phone number provided."
                        ),
                        None,
                    )
                
                update_data = {"phone": phone_number}
                user_service.update_user_details(user_id, update_data)
                
                response = self.response_formatter.format_response(
                    intent, "success",
                    message=f"Your phone number has been updated to '{phone_number}' successfully! ✅"
                )
                logger.info(f"User {user_id} phone number updated to {phone_number}")
                return IntentHandlerResult(response, 200)
            
            elif intent == "update_user_details":
                allowed_fields = {
                    "fullname",
                    "phone_number",
                    "location",
                    "occupation",
                    "address",
                    "company",
                }
                slot_to_field = {"phone_number": "phone"}

                update_data = {}
                for slot, value in slots.items():
                    if slot in allowed_fields and value is not None:
                        field_name = slot_to_field.get(slot, slot)
                        update_data[field_name] = value
                        logger.info(f"Preparing to update {field_name} for user {user_id}")
                
                if not update_data:
                    return IntentHandlerResult(
                        self.response_formatter.format_response(
                            intent, "error", message="No valid fields to update provided."
                        ),
                        None,
                    )
                
                user_service.update_user_details(user_id, update_data)
                response = self.response_formatter.format_response(intent, "success", message="Your profile has been updated successfully! ✅")
                logger.info(f"User {user_id} profile updated with fields: {list(update_data.keys())}")
                return IntentHandlerResult(response, 200)
                
            elif intent == "view_user_profile":
                profile = user_service.get_user_profile(user_id)
                
                profile_details = f"""
                📋 *Your Profile:*
                - Name: {profile.get('fullname', 'N/A')}
                - Account Email: {profile.get('email', 'N/A')}
                - Sender Email: {profile.get('sender_email') or 'Not configured'}
                - Phone: {profile.get('phone', 'N/A')}
                - Location: {profile.get('location', 'N/A')}
                - Occupation: {profile.get('occupation', 'N/A')}
                - Company: {profile.get('company', 'N/A')}
                """
                response = self.response_formatter.format_response(
                    intent, "success", message=profile_details
                )
                logger.info(f"User {user_id} viewed their profile")
                return IntentHandlerResult(response, 200)
            
            else:
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        intent, "error", message="Unknown user management intent."
                    ),
                    None,
                )
            
        except HTTPException as e:
            logger.error(f"HTTP Error in user management intent: {str(e)}")
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    intent, "error", message=str(e.detail)
                ),
                None,
            )
        except Exception as e:
            logger.error(f"Error processing user management intent: {str(e)}", exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    intent, "error", message="An error occurred while processing your request."
                ),
                None,
            )
        finally:
            db.close()
        
    def generate_receipt_after_payment(self, transaction_id: str, user_id: str, intent: str,
                                  amount: Decimal, status: str, sender: str, receiver: str,
                                  sender_name: str, receiver_name: str,
                                  sender_provider: str, receiver_provider: str,
                                  payment_method: str, timestamp: datetime) -> str:
        """Generate receipt image and save to Azure Blob Storage"""
        try:

            logger.info(f"[RECEIPT] Generating receipt for transaction: {transaction_id}")
            
            # Map intent to transaction type for receipt
            transaction_type_map = {
                "buy_airtime": "Airtime Purchase",
                "send_money": "Money Transfer", 
                "pay_bill": "Bill Payment",
                "get_loan": "Loan Disbursement"
            }
            
            # Prepare receipt data
            receipt_data = {
                'transaction_id': transaction_id,
                'user_id': user_id,
                'transaction_type': transaction_type_map.get(intent, "Payment"),
                'amount': str(amount),
                'status': status,
                'sender_account': sender,
                'receiver_account': receiver,
                'sender_name': sender_name,
                'receiver_name': receiver_name,
                'sender_provider': sender_provider,
                'receiver_provider': receiver_provider,
                'payment_method': payment_method,
                'timestamp': timestamp
            }
            
            # Add loan-specific fields if it's a loan transaction
            if intent == "get_loan":
                receipt_data.update({
                    'interest_rate': '5',  # You might want to get this from your data
                    'loan_period': '30 days',  # Default or from slots
                    'expected_pay_date': (timestamp + timedelta(days=30)).strftime("%b %d, %Y"),
                    'penalty_rate': '2'  # Default penalty rate
                })
            
            # Generate receipt image
            receipt_generator = ReceiptGenerator()
            base64_data_url = receipt_generator.generate_receipt_image(receipt_data)
            
            # Extract base64 data from data URL
            base64_data = base64_data_url.split(',')[1]
            image_data = base64.b64decode(base64_data)
            
            # Create file-like object from image data
            image_file = io.BytesIO(image_data)
            
            # Generate filename with date and user ID
            date_str = timestamp.strftime("%Y%m%d_%H%M%S")
            filename = f"{date_str}_{user_id}_{transaction_id}.png"
            
            # Upload to Azure Blob Storage
            storage_service = StorageService()
            blob_url = storage_service.upload_file(
                file_obj=image_file,
                file_name=filename,
                content_type="image/png",
                folder="records-files",
            )
            
            logger.info(f"[RECEIPT] Receipt saved to Azure Storage: {blob_url}")
            return blob_url

        except Exception as e:
            logger.error(f"[RECEIPT] Error generating/saving receipt: {str(e)}")
            # Return a fallback or empty string if receipt generation fails
            return ""

    def _get_processing_message(self, intent: str, slots: Dict, result: Any) -> str:
        """Generate message indicating payment is being processed"""
        if intent == "send_money":
            receiver_name = slots.get('receiver_name', 'Recipient')
            recipient_phone = slots.get('recipient')
            receiver_provider = slots.get('receiver_provider', 'the recipient provider')
            return f"Your Transfer to {recipient_phone} ({receiver_name}) on {receiver_provider} is being processed. Transaction ID: {result.transactionId}"

        processing_messages = {
            "buy_airtime": f"Airtime purchase of GHS {slots.get('amount')} for {slots.get('phone_number')} is being processed. Transaction ID: {result.transactionId}",
            "pay_bill": f"Bill payment of GHS {slots.get('amount')} is being processed. Transaction ID: {result.transactionId}",
            "get_loan": f"Loan application for GHS {slots.get('loan_amount')} is being processed. Transaction ID: {result.transactionId}"
        }
        return processing_messages.get(intent, "Your payment is being processed. Transaction ID: {result.transactionId}")

    def _get_success_message(self, intent: str, slots: Dict, result: Any) -> str:
        """Generate success message based on intent"""
        if intent == "send_money":
            receiver_name = slots.get('receiver_name', 'Recipient')
            recipient_phone = slots.get('recipient')
            receiver_provider = slots.get('receiver_provider', 'the recipient provider')
            return f"Your Transfer to {recipient_phone} ({receiver_name}) on {receiver_provider} has been successfully completed"

        success_messages = {
            "buy_airtime": f"✅ Airtime of GHS {slots.get('amount')} sent to {slots.get('phone_number')}. Transaction ID: {result.transactionId}",
            "pay_bill": f"✅ Bill payment of GHS {slots.get('amount')} processed. Transaction ID: {result.transactionId}",
            "get_loan": f"✅ Loan of GHS {slots.get('loan_amount')} application submitted. Transaction ID: {result.transactionId}"
        }
        return success_messages.get(intent, "Payment processed successfully")

    @staticmethod
    def _parse_merchant_scoped_user_id(user_id: str) -> tuple:
        """If ``user_id`` is ``<merchant_users.id>:<customer_channel>``, return (merchant_id, customer_channel)."""
        if not user_id or ":" not in user_id:
            return None, user_id
        company_id, _, rest = user_id.partition(":")
        company_id = company_id.strip()
        rest = (rest or "").strip()
        if not company_id or not rest:
            return None, user_id
        return company_id, rest

    def _get_user_data(self, user_id: str) -> Optional[Dict]:
        """Fetch user data for personalized processing (merchant row, optionally scoped to a customer channel)."""
        try:
            db = SessionLocal()
            user_service = UserService(db)

            merchant_id, channel_user_id = self._parse_merchant_scoped_user_id(user_id)

            if merchant_id:
                merchant = db.query(User).filter(User.id == merchant_id).first()
                if not merchant:
                    return None
                return {
                    # End-user identifier (phone / external id) for slots, RAG metadata, etc.
                    "user_id": channel_user_id,
                    "customer_phone": channel_user_id,
                    # Merchant account used for FKs, RAG tenant, products, orders.
                    "db_user_id": merchant.id,
                    "merchant_id": merchant.id,
                    "is_customer_session": True,
                    "email": merchant.email,
                    "fullname": merchant.fullname,
                    "company": merchant.company,
                    "organization_workplace": merchant.organization_workplace,
                    "created_at": merchant.created_at.isoformat()
                    if merchant.created_at
                    else None,
                }

            user = user_service.get_user_by_phone(channel_user_id)

            if user:
                return {
                    "user_id": user.phone,
                    "customer_phone": user.phone,
                    "db_user_id": user.id,
                    "email": user.email,
                    "fullname": user.fullname,
                    "company": user.company,
                    "organization_workplace": user.organization_workplace,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                }
            return None

        except Exception as e:
            logger.error(f"Error fetching user data for {user_id}: {e}")
            return None
        finally:
            db.close()

    def _upload_product_photo_from_media(
        self, user_id: str, media_context: Dict[str, Any]
    ) -> Optional[str]:
        """Upload an incoming chat image to product storage and return its URL."""
        image_b64 = media_context.get("image_base64")
        if not image_b64:
            return None

        try:
            image_bytes = base64.b64decode(image_b64)
            mime = media_context.get("image_mime_type") or "image/jpeg"
            ext = "jpg"
            if "/" in mime:
                ext_candidate = mime.split("/")[-1].lower()
                if ext_candidate in {"jpeg", "jpg", "png", "gif", "webp"}:
                    ext = "jpg" if ext_candidate == "jpeg" else ext_candidate

            safe_user = (user_id or "unknown").replace("/", "_").replace("\\", "_")
            filename = f"{safe_user}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.{ext}"
            storage_service = StorageService()
            return storage_service.upload_file(
                io.BytesIO(image_bytes),
                filename,
                content_type=mime,
                folder=StorageFolder.product_images,
            )
        except Exception as e:
            logger.error(
                f"[PRODUCT_PHOTO] Failed to upload product image for {user_id}: {e}",
                exc_info=True,
            )
            return None

    def _process_media_inputs(
        self,
        user_id: str,
        image_media_id: Optional[str] = None,
        image_url: Optional[str] = None,
        audio_media_id: Optional[str] = None,
        audio_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process multimodal inputs (images and audio) from WhatsApp or direct URLs.
        
        Handles:
        - Images: Converts to base64 and extracts MIME type
        - Audio: Downloads/processes and prepares for transcription
        
        Args:
            user_id: User identifier
            image_media_id: WhatsApp media ID for image
            image_url: Direct URL to image (fallback if media_id not available)
            audio_media_id: WhatsApp media ID for audio
            audio_url: Direct URL to audio (fallback if media_id not available)
            
        Returns:
            Dictionary with processed media context:
            {
                'image_base64': base64-encoded image data,
                'image_url': URL to image,
                'image_mime_type': MIME type of image,
                'audio_bytes': Raw audio bytes,
                'audio_filename': Filename for audio,
                'audio_mime_type': MIME type of audio
            }
        """
        from core.nlu.service.media_processor import MediaProcessor
        
        media_processor = MediaProcessor()
        media_context = {}
        
        # Process image if provided
        if image_media_id or image_url:
            try:
                logger.info(f"[MEDIA_PROCESSING] Processing image for user {user_id} (media_id: {bool(image_media_id)}, url: {bool(image_url)})")
                image_data = media_processor.process_image(
                    media_id=image_media_id or "",
                    media_url=image_url
                )
                
                if image_data:
                    media_context["image_base64"] = image_data.get("base64")
                    media_context["image_url"] = image_data.get("url")
                    media_context["image_mime_type"] = image_data.get("mime_type")
                    logger.info(
                        f"[MEDIA_PROCESSING] Image processed successfully for user {user_id} "
                        f"(type: {image_data.get('mime_type')}, has_base64: {bool(image_data.get('base64'))})"
                    )
                else:
                    logger.warning(f"[MEDIA_PROCESSING] Failed to process image for user {user_id} (no data returned)")
                    
            except Exception as e:
                logger.error(f"[MEDIA_PROCESSING] Error processing image for user {user_id}: {str(e)}", exc_info=True)
        
        # Process audio if provided
        if audio_media_id or audio_url:
            try:
                logger.info(f"[MEDIA_PROCESSING] Processing audio for user {user_id} (media_id: {bool(audio_media_id)}, url: {bool(audio_url)})")
                audio_data = media_processor.process_audio(
                    media_id=audio_media_id or "",
                    media_url=audio_url
                )
                
                if audio_data:
                    media_context["audio_bytes"] = audio_data.get("bytes")
                    media_context["audio_filename"] = audio_data.get("filename")
                    media_context["audio_mime_type"] = audio_data.get("mime_type")
                    audio_size_kb = audio_data.get('size', 0) / 1024
                    logger.info(
                        f"[MEDIA_PROCESSING] Audio processed successfully for user {user_id} "
                        f"(type: {audio_data.get('mime_type')}, size: {audio_size_kb:.1f}KB, filename: {audio_data.get('filename')})"
                    )
                else:
                    logger.warning(f"[MEDIA_PROCESSING] Failed to process audio for user {user_id} (no data returned)")
                    
            except Exception as e:
                logger.error(f"[MEDIA_PROCESSING] Error processing audio for user {user_id}: {str(e)}", exc_info=True)
        
        if media_context:
            logger.info(f"[MEDIA_PROCESSING] Media context prepared for user {user_id}: {list(media_context.keys())}")
        else:
            logger.warning(f"[MEDIA_PROCESSING] No media context could be created for user {user_id}")
        
        return media_context
