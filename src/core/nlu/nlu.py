
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
from core.nlu.emitters.response import ResponseFormatter
from core.nlu.service.intentprocessor import IntentProcessor
from core.nlu.service.intents import IntentDetector
from core.nlu.service.slot_manager import SlotManager
from core.nlu.service.conversation_manager import ConversationManager
from core.nlu.service.intent_handler_result import IntentHandlerResult
from core.nlu.service.process_message_result import ProcessMessageResult
from core.nlu.service.payment_command_parser import try_parse_payment_command
from core.nlu.service.payment_confirmation import (
    is_affirmative_response,
    is_declining_response,
    resolve_payment_slots,
    should_handle_payment_confirmation,
)
from core.nlu.service.security import SecurityManager
from core.nlu.service.date_selection_manager import DateSelectionManager, DateOption
from core.nlu.service.account_access import (
    channel_type,
    extract_phone_from_message,
    find_registered_user,
    friendly_account_required_message,
    is_telegram_link_attempt,
    resolve_telegram_user,
    telegram_link_success_message,
)
from core.user.service.user_service import UserService
from utilities.dbconfig import SessionLocal
from core.auth.dto.request.user_create import UserCreateRequest
from core.user.model.User import User
from decimal import Decimal
from utilities.crypto import decrypt_secret


logger = logging.getLogger(__name__)

_DELETE_TASK_RE = re.compile(
    r"^\s*(?:delete|remove|cancel)(?:\s+(?:task|item))?\s+#?(?:T)?(\d+)\s*$",
    re.IGNORECASE,
)
_UPDATE_TASK_RE = re.compile(
    r"^\s*(?:update|change|edit)(?:\s+(?:task|item))?\s+#?(?:T)?(\d+)\s+(?:to\s+)?(.+)$",
    re.IGNORECASE,
)

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
        self._telegram_context_user: Optional[User] = None
        self._tts_client = None

    @property
    def tts_client(self):
        if self._tts_client is None:
            from core.tts.tts_client import TTSClient

            self._tts_client = TTSClient()
        return self._tts_client

    def set_telegram_context_user(self, user: Optional[User]) -> None:
        """Reuse the Telegram user resolved by the webhook layer for this request."""
        self._telegram_context_user = user

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
    def _try_parse_delete_task_command(user_message: str) -> Optional[int]:
        match = _DELETE_TASK_RE.match((user_message or "").strip())
        if not match:
            return None
        try:
            return int(match.group(1))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _try_parse_update_task_command(user_message: str) -> Optional[Dict[str, str]]:
        match = _UPDATE_TASK_RE.match((user_message or "").strip())
        if not match:
            return None
        from core.memory.service.task_management_service import TaskManagementService

        try:
            task_number = str(int(match.group(1)))
        except (TypeError, ValueError):
            return None
        payload = TaskManagementService.parse_update_payload(match.group(2))
        slots = {"task_number": task_number}
        slots.update({k: v for k, v in payload.items() if v})
        return slots

    def _clear_payment_confirmation_state(self, user_id: str) -> None:
        state = self.conversation_manager.get_conversation_state(user_id)
        state.waiting_for_payment_confirmation = False
        state.pending_payment_dto = {}
        state.current_intent = ""
        state.collected_slots = {}
        self.conversation_manager._save_conversation_state(state)

    def _try_handle_payment_confirmation(
        self,
        user_id: str,
        user_message: str,
        state,
    ) -> Optional[ProcessMessageResult]:
        if not should_handle_payment_confirmation(
            user_message=user_message,
            current_intent=state.current_intent,
            waiting_for_payment_confirmation=state.waiting_for_payment_confirmation,
            collected_slots=state.collected_slots,
            pending_payment_dto=state.pending_payment_dto,
            conversation_history=state.conversation_history,
        ):
            return None

        if is_declining_response(user_message):
            response = self.response_formatter.format_response("", "payment_cancelled")
            self._clear_payment_confirmation_state(user_id)
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        if not is_affirmative_response(user_message):
            return None

        slots = resolve_payment_slots(
            collected_slots=state.collected_slots,
            pending_payment_dto=state.pending_payment_dto,
            conversation_history=state.conversation_history,
        )
        if not slots:
            response = self.response_formatter.format_response(
                "",
                "confirm_again",
                message="I still need the payment amount. For example: send 2 cedis to Anna 0207926310.",
            )
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        user_data = self._get_user_data(user_id)
        if not self._has_registered_account(user_id, user_data):
            response = self.response_formatter.format_response(
                "", "account_required", channel=channel_type(user_id)
            )
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        outcome = self._execute_action(
            user_id,
            "make_payment",
            slots,
            user_message,
            state.conversation_history,
        )
        result = self._terminal_listener_apply(user_id, outcome)
        self._clear_payment_confirmation_state(user_id)
        self.conversation_manager.update_conversation_history(user_id, "assistant", result.text)
        return result



    def _conversation_completion_tool(self, user_id: str, success_message: str) -> str:
        """After a fulfilled intent (HTTP 200): keep success text and prompt for more help."""
        state = self.conversation_manager.get_conversation_state(user_id)
        state.conversation_lifecycle = "awaiting_followup_help"
        self.conversation_manager._save_conversation_state(state)
        follow = "\n\nIs there anything else I can help you with?"
        return f"{(success_message or '').strip()}{follow}"

    def _terminal_listener_apply(self, user_id: str, outcome: IntentHandlerResult) -> ProcessMessageResult:
        if outcome.http_status == 200:
            msg = self._conversation_completion_tool(user_id, outcome.message)
            return ProcessMessageResult(
                text=msg,
                audio_bytes=outcome.audio_bytes,
                audio_mime_type=outcome.audio_mime_type,
            )
        return ProcessMessageResult(text=outcome.message)

    @staticmethod
    def _as_result(message: str) -> ProcessMessageResult:
        return ProcessMessageResult(text=message)


    def process_message(
        self, 
        user_id: str, 
        user_message: str, 
        image_media_id: Optional[str] = None,
        image_url: Optional[str] = None,
        audio_media_id: Optional[str] = None,
        audio_url: Optional[str] = None
    ) -> ProcessMessageResult:
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

        if is_telegram_link_attempt(user_message or "") and channel_type(user_id) == "telegram":
            self.conversation_manager.update_conversation_history(user_id, "user", user_message)
            db = self.db_session or SessionLocal()
            should_close = self.db_session is None
            try:
                linked_user = resolve_telegram_user(
                    db,
                    user_id,
                    user_message,
                    conversation_manager=self.conversation_manager,
                )
                if linked_user:
                    response = telegram_link_success_message(linked_user)
                else:
                    response = friendly_account_required_message(
                        "telegram",
                        phone=extract_phone_from_message(user_message or ""),
                    )
            finally:
                if should_close:
                    db.close()
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        logger.info("Received message from %s: %s", user_id, (user_message or "")[:200])

        if state.conversation_lifecycle == "awaiting_followup_help":
            self.conversation_manager.update_conversation_history(user_id, "user", user_message)
            if self._is_declining_more_help(user_message):
                thanks = "You're welcome. Reach out anytime you need help."
                self.conversation_manager.update_conversation_history(user_id, "assistant", thanks)
                self.conversation_manager.finalize_completed_session(user_id)
                return self._as_result(thanks)
            state.conversation_lifecycle = "active"
            self.conversation_manager._save_conversation_state(state)
        else:
            self.conversation_manager.update_conversation_history(user_id, "user", user_message)

        payment_confirmation_result = self._try_handle_payment_confirmation(
            user_id,
            user_message,
            state,
        )
        if payment_confirmation_result is not None:
            return payment_confirmation_result

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
        quick_delete_index = self._try_parse_delete_task_command(user_message)
        quick_update_slots = (
            None if quick_delete_index is not None else self._try_parse_update_task_command(user_message)
        )
        quick_payment_slots = (
            None
            if quick_delete_index is not None or quick_update_slots is not None
            else try_parse_payment_command(user_message)
        )
        if quick_delete_index is not None:
            intent = "delete_task"
            extracted_slots = {"task_number": str(quick_delete_index)}
            missing_slots = []
            logger.info("Parsed delete-task command for user %s: index=%s", user_id, quick_delete_index)
        elif quick_update_slots is not None:
            intent = "update_task"
            extracted_slots = quick_update_slots
            missing_slots = []
            logger.info("Parsed update-task command for user %s: slots=%s", user_id, quick_update_slots)
        elif quick_payment_slots is not None:
            intent = "make_payment"
            extracted_slots = quick_payment_slots
            missing_slots = []
            logger.info(
                "Parsed payment command for user %s: slots=%s",
                user_id,
                quick_payment_slots,
            )
        else:
            intent, extracted_slots, missing_slots = self.intent_detector.detect_intent_and_slots(
                user_message, state.conversation_history, state.current_intent, media_context
            )

        # If the model explicitly reported it cannot process the image, ask the user
        if intent == "cannot_process_image":
            logger.info("Model cannot process image for user %s; asking for description", user_id)
            response = self.response_formatter.format_response("", "ask_for_image_description")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)
        
        # If the intent is not clear due to low confidence, return appropriate response
        if intent == "intent_not_clear":
            logger.info("Intent not clear for user %s", user_id)
            response = self.response_formatter.format_response("", "intent_not_clear")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        from core.nlu.config import INTENTS
        if intent == "unknown" or intent not in INTENTS:
            logger.info("Unknown intent for user %s (intent=%s)", user_id, intent)
            response = self.response_formatter.format_response("", "intent_not_clear")
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)
        
        logger.info("Detected intent=%s missing=%s", intent, missing_slots)

        user_data = self._get_user_data(user_id)
        if self._requires_registered_account(intent) and not self._has_registered_account(user_id, user_data):
            response = self.response_formatter.format_response(
                "", "account_required", channel=channel_type(user_id)
            )
            self.conversation_manager.update_conversation_history(user_id, "assistant", response)
            return self._as_result(response)

        merchant_id, channel_user_id = self._parse_merchant_scoped_user_id(user_id)
        if merchant_id:
            conversational_only = set(INTENT_CATEGORIES.get("conversational", []))
            task_management = set(INTENT_CATEGORIES.get("task_management", []))
            allowed = conversational_only
            if self._is_merchant_owner_channel(user_data, channel_user_id):
                allowed |= task_management
            if intent not in allowed:
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

        if intent in ("delete_task", "update_task", "manage_tasks"):
            state.collected_slots = {}
        state.collected_slots.update(validated_slots)
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

            is_task_delete = self._try_parse_delete_task_command(user_message) is not None
            if not is_task_delete and any(
                keyword == user_msg_lower or user_msg_lower.startswith(keyword + " ")
                for keyword in cancellation_keywords
            ):
                logger.info(f"[CANCELLATION] User {user_id} cancelled {state.current_intent} during slot collection")
                response = "Okay, I've cancelled that. How else can I help you?"

                state.current_intent = ""
                state.collected_slots = {}
                self.conversation_manager._save_conversation_state(state)

                self.conversation_manager.update_conversation_history(user_id, "assistant", response)
                return self._as_result(response)

        # Check for missing required slots
        current_missing = self.slot_manager.get_missing_slots(intent, state.collected_slots)

        if current_missing:
            prompt = self.slot_manager.generate_slot_prompt(intent, current_missing)
            result = self._as_result(
                self.response_formatter.format_response(
                    intent, "missing_slots", prompt=prompt
                )
            )

        else:
            # All slots collected, execute action directly
            slots_to_execute = state.collected_slots.copy()
            handler_outcome = self._execute_action(
                user_id, intent, slots_to_execute, user_message, state.conversation_history
            )
            result = self._terminal_listener_apply(user_id, handler_outcome)
        
        # Add assistant response to history
        self.conversation_manager.update_conversation_history(user_id, "assistant", result.text)

        # Clear collected slots if action was executed
        if not current_missing:
            self.conversation_manager.clear_collected_slots(user_id)
            state = self.conversation_manager.get_conversation_state(user_id)
            state.current_intent = ""
            self.conversation_manager._save_conversation_state(state)
        
        return result
    
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
            result = self._terminal_listener_apply(user_id, outcome)
            state.waiting_for_pin = False
            state.pending_action = {}
            state.collected_slots = {}
            self.conversation_manager._save_conversation_state(state)
            response = result.text
        else:
            # Invalid PIN
            response = self.response_formatter.format_response("", "invalid_pin")
            # Keep waiting for PIN

        self.conversation_manager.update_conversation_history(user_id, "assistant", response)
        return response

    def _execute_action(self, user_id: str, intent: str, slots: Dict, user_message: str = "", conversation_history: List[Dict] = None) -> IntentHandlerResult:
        """Execute a detected intent."""
        try:
            return self._process_non_payment_intent(user_id, intent, user_message, conversation_history, slots)
        except Exception as e:
            import traceback
            print(f"[EXECUTE_ACTION] ERROR: {e}")
            traceback.print_exc()
            if str(e) == "registered_user_not_found":
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )
            return IntentHandlerResult(
                self.response_formatter.format_response(intent, "error", message=str(e)),
                None,
            )

    def _process_non_payment_intent(self, user_id: str, intent: str, user_message: str, conversation_history: List[Dict], slots: Dict) -> IntentHandlerResult:
        """Process non-payment intents; http_status 200 means fulfilled (terminal success)."""
        conversational_intents = INTENT_CATEGORIES["conversational"]
        payment_intents = INTENT_CATEGORIES.get("payment", [])
        expense_report_intents = INTENT_CATEGORIES["expense_report"]
        user_management_intents = INTENT_CATEGORIES.get("user_management", [])
        task_management_intents = INTENT_CATEGORIES.get("task_management", [])
        email_intents = INTENT_CATEGORIES.get("email", [])
        video_generation_intents = INTENT_CATEGORIES.get("video_generation", [])
        image_generation_intents = INTENT_CATEGORIES.get("image_generation", [])
        
        logger.info(f"Processing non-payment intent '{intent}' for user {user_id}")

        user_data = self._get_user_data(user_id)

        # Public-site customers chat as ``<merchant_id>:<phone>`` — never run merchant admin flows.
        if user_data and user_data.get("is_customer_session"):
            _, channel_user_id = self._parse_merchant_scoped_user_id(user_id)
            task_management_intents = set(INTENT_CATEGORIES.get("task_management", []))
            if intent in task_management_intents and not self._is_merchant_owner_channel(
                user_data, channel_user_id
            ):
                logger.info(
                    "Customer session %s: redirecting admin intent '%s' to business_conversation",
                    user_id,
                    intent,
                )
                intent = "business_conversation"
            elif intent not in conversational_intents and intent not in task_management_intents:
                logger.info(
                    "Customer session %s: redirecting admin intent '%s' to business_conversation",
                    user_id,
                    intent,
                )
                intent = "business_conversation"
        
        if intent in conversational_intents:
            msg = self._process_conversational(
                user_id=user_id,
                intent=intent,
                user_message=user_message,
                conversation_history=conversation_history,
                slots=slots,
                user_data=user_data,
            )
            return IntentHandlerResult(msg, None)
        elif intent in payment_intents:
            if not self._has_registered_account(user_id, user_data):
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )
            msg = self.intent_processor.process_payment_intent(
                intent,
                slots,
                user_data,
            )
            return IntentHandlerResult(msg, 200)
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
        elif intent in user_management_intents:
            return self._process_user_management_intent(user_id, intent, slots)
        elif intent in task_management_intents:
            if intent == "add_task":
                return self._process_add_task_intent(user_id, slots, user_data)
            if intent == "manage_tasks":
                return self._process_manage_tasks_intent(user_id, user_data)
            if intent == "delete_task":
                return self._process_delete_task_intent(user_id, slots, user_data)
            if intent == "update_task":
                return self._process_update_task_intent(user_id, slots, user_data)
            return self._process_briefing_intent(user_id, intent, user_data, user_message)
        else:
            # Fallback for unhandled intents
            return IntentHandlerResult(
                self.response_formatter.format_response(intent, "error", message="Intent not supported"),
                None,
            )

    @staticmethod
    def _requires_registered_account(intent: str) -> bool:
        protected = set()
        for key in ("payment", "task_management", "email", "user_management", "image_generation", "video_generation"):
            protected.update(INTENT_CATEGORIES.get(key, []))
        return intent in protected

    def _telegram_link_state(self, user_id: str) -> tuple[Optional[str], Optional[str]]:
        state = self.conversation_manager.get_conversation_state(user_id)
        linked_phone = getattr(state, "viin_linked_phone", None)
        linked_user_id = getattr(state, "viin_linked_user_id", None)
        return linked_phone, linked_user_id

    def _lookup_registered_user(self, user_id: str) -> Optional[User]:
        if self._telegram_context_user and channel_type(user_id) == "telegram":
            return self._telegram_context_user

        db = self.db_session or SessionLocal()
        should_close = self.db_session is None
        try:
            if channel_type(user_id) == "telegram":
                return resolve_telegram_user(
                    db,
                    user_id,
                    None,
                    conversation_manager=self.conversation_manager,
                )
            linked_phone, linked_user_id = self._telegram_link_state(user_id)
            return find_registered_user(
                db,
                user_id,
                linked_phone=linked_phone,
                linked_user_id=linked_user_id,
            )
        finally:
            if should_close:
                db.close()

    def _has_registered_account(
        self, user_id: str, user_data: Optional[Dict[str, Any]]
    ) -> bool:
        if (user_data or {}).get("db_user_id"):
            return True
        return self._lookup_registered_user(user_id) is not None

    def _resolve_internal_user_id(
        self, user_id: str, user_data: Optional[Dict[str, Any]]
    ) -> str:
        internal_user_id = (user_data or {}).get("db_user_id")
        if internal_user_id:
            return str(internal_user_id)

        user = self._lookup_registered_user(user_id)
        if user:
            return str(user.id)
        raise ValueError("registered_user_not_found")

    @staticmethod
    def _is_merchant_owner_channel(
        user_data: Optional[Dict[str, Any]], channel_user_id: str
    ) -> bool:
        """True when the chatter is the merchant account (e.g. owner testing the webhook)."""
        if not user_data or not user_data.get("is_customer_session"):
            return True

        merchant_id = str(user_data.get("merchant_id") or user_data.get("db_user_id") or "")
        if not merchant_id:
            return False

        db = SessionLocal()
        try:
            merchant = db.query(User).filter(User.id == merchant_id).first()
            if not merchant:
                return False

            from utilities.phone_utils import normalize_ghana_phone_number

            channel_norm = normalize_ghana_phone_number(channel_user_id or "")
            for raw in (merchant.phone, merchant.whatsapp_number):
                if raw and normalize_ghana_phone_number(raw) == channel_norm:
                    return True

            chatter = UserService(db).find_user_by_phone(channel_user_id)
            return bool(chatter and chatter.id == merchant_id)
        finally:
            db.close()

    def _process_conversational(
        self,
        *,
        user_id: str,
        intent: str,
        user_message: str,
        conversation_history: List[Dict],
        slots: Dict,
        user_data: Optional[Dict[str, Any]],
    ) -> str:
        """Answer conversational intents with Postgres-backed task memory context."""
        from core.memory.service.task_memory_context import TaskMemoryContextService

        try:
            internal_user_id = self._resolve_internal_user_id(user_id, user_data)
        except ValueError:
            return self.response_formatter.format_response(
                "", "account_required", channel=channel_type(user_id)
            )

        db = self.db_session or SessionLocal()
        should_close = self.db_session is None
        try:
            task_context = TaskMemoryContextService(db).build_context(
                owner_user_id=internal_user_id
            )
        finally:
            if should_close:
                db.close()

        return self.intent_processor.process_conversational_intent(
            intent,
            user_message,
            conversation_history,
            slots,
            user_id=internal_user_id,
            user_data=user_data,
            task_context=task_context,
        )

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

    def _process_briefing_intent(
        self,
        user_id: str,
        intent: str,
        user_data: Optional[Dict[str, Any]],
        user_message: str = "",
    ) -> IntentHandlerResult:
        """Build a daily, weekly, or monthly to-do briefing from memory lists and reminders."""
        from core.memory.service.briefing_service import BriefingPeriod, BriefingService

        _PERIOD_BY_INTENT = {
            "daily_briefing": BriefingPeriod.DAILY,
            "weekly_briefing": BriefingPeriod.WEEKLY,
            "monthly_briefing": BriefingPeriod.MONTHLY,
        }

        db = SessionLocal()
        try:
            try:
                internal_user_id = self._resolve_internal_user_id(user_id, user_data)
            except ValueError:
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )
            svc = BriefingService(db)
            lowered = (user_message or "").lower()
            if "yesterday" in lowered or "last day" in lowered:
                tasks = svc.collect_tasks_due_on_day(
                    owner_user_id=internal_user_id,
                    day_offset=-1,
                )
                msg = svc.build_due_day_briefing(
                    owner_user_id=internal_user_id,
                    day_offset=-1,
                )
            else:
                period = _PERIOD_BY_INTENT.get(intent, BriefingPeriod.DAILY)
                tasks = svc.collect_tasks(owner_user_id=internal_user_id, period=period)
                msg = svc.format_briefing(tasks=tasks, period=period)

            logger.info(
                "Generated %s briefing for user %s (owner_user_id=%s)",
                "yesterday" if "yesterday" in lowered or "last day" in lowered else intent,
                user_id,
                internal_user_id,
            )
            audio_bytes = self.tts_client.synthesize_briefing(msg)
            if audio_bytes:
                logger.info("Generated briefing audio for user %s (%s bytes)", user_id, len(audio_bytes))
            return IntentHandlerResult(msg, 200, audio_bytes=audio_bytes)
        except Exception as e:
            logger.error("Briefing failed for user %s: %s", user_id, e, exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    intent, "error", message="I couldn't generate your briefing right now."
                ),
                None,
            )
        finally:
            db.close()

    @staticmethod
    def _managed_task_refs(state) -> List[Dict[str, Any]]:
        refs = list(getattr(state, "pending_managed_tasks", None) or [])
        if refs:
            return refs
        return list(getattr(state, "pending_briefing_tasks", None) or [])

    @staticmethod
    def _save_managed_task_refs(state, task_refs: List[Dict[str, Any]]) -> None:
        from core.memory.service.task_management_service import make_ref_id

        renumbered: List[Dict[str, Any]] = []
        for i, ref in enumerate(task_refs, start=1):
            updated = dict(ref)
            updated["index"] = i
            updated["ref_id"] = make_ref_id(i)
            renumbered.append(updated)
        state.pending_managed_tasks = renumbered
        state.pending_briefing_tasks = renumbered

    def _process_manage_tasks_intent(
        self,
        user_id: str,
        user_data: Optional[Dict[str, Any]],
    ) -> IntentHandlerResult:
        """List all tasks with IDs for individual update/delete."""
        from core.memory.service.task_management_service import TaskManagementService

        db = SessionLocal()
        try:
            try:
                internal_user_id = self._resolve_internal_user_id(user_id, user_data)
            except ValueError:
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )

            svc = TaskManagementService(db)
            tasks = svc.list_manageable_tasks(owner_user_id=internal_user_id)
            msg = svc.format_manage_list(tasks=tasks)

            state = self.conversation_manager.get_conversation_state(user_id)
            self._save_managed_task_refs(state, svc.tasks_to_refs(tasks))
            self.conversation_manager._save_conversation_state(state)

            logger.info(
                "Generated manage-tasks list for user %s (owner=%s, count=%s)",
                user_id,
                internal_user_id,
                len(tasks),
            )
            return IntentHandlerResult(msg, 200)
        except Exception as e:
            logger.error("Manage tasks failed for user %s: %s", user_id, e, exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "manage_tasks",
                    "error",
                    message="I couldn't load your tasks right now.",
                ),
                None,
            )
        finally:
            db.close()

    def _process_delete_task_intent(
        self,
        user_id: str,
        slots: Dict[str, Any],
        user_data: Optional[Dict[str, Any]],
    ) -> IntentHandlerResult:
        """Remove a task from the user's last manage-tasks list."""
        from core.memory.service.task_management_service import TaskManagementService, parse_task_number

        try:
            task_index = parse_task_number(slots.get("task_number"))
        except ValueError:
            return IntentHandlerResult(
                'Please say which item to remove, e.g. "delete T1" or "remove 2".',
                None,
            )

        db = SessionLocal()
        try:
            try:
                internal_user_id = self._resolve_internal_user_id(user_id, user_data)
            except ValueError:
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )

            state = self.conversation_manager.get_conversation_state(user_id)
            task_refs = self._managed_task_refs(state)
            svc = TaskManagementService(db)
            msg = svc.delete_task_at_index(
                owner_user_id=internal_user_id,
                task_refs=task_refs,
                index=task_index,
            )

            if task_refs:
                del task_refs[task_index - 1]
                self._save_managed_task_refs(state, task_refs)
                self.conversation_manager._save_conversation_state(state)

            logger.info(
                "Deleted managed task #%s for user %s (owner=%s)",
                task_index,
                user_id,
                internal_user_id,
            )
            return IntentHandlerResult(msg, 200)
        except ValueError as e:
            return IntentHandlerResult(str(e), None)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "delete_task", "error", message=detail
                ),
                None,
            )
        except Exception as e:
            logger.error("Delete task failed for user %s: %s", user_id, e, exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "delete_task",
                    "error",
                    message="I couldn't remove that item right now. Please try again.",
                ),
                None,
            )
        finally:
            db.close()

    def _process_update_task_intent(
        self,
        user_id: str,
        slots: Dict[str, Any],
        user_data: Optional[Dict[str, Any]],
    ) -> IntentHandlerResult:
        """Update a task from the user's last manage-tasks list."""
        from core.memory.service.task_management_service import TaskManagementService, parse_task_number

        try:
            task_index = parse_task_number(slots.get("task_number"))
        except ValueError:
            return IntentHandlerResult(
                'Please say which item to update, e.g. "update T1 to buy eggs".',
                None,
            )

        db = SessionLocal()
        try:
            try:
                internal_user_id = self._resolve_internal_user_id(user_id, user_data)
            except ValueError:
                return IntentHandlerResult(
                    self.response_formatter.format_response(
                        "", "account_required", channel=channel_type(user_id)
                    ),
                    None,
                )

            state = self.conversation_manager.get_conversation_state(user_id)
            task_refs = self._managed_task_refs(state)
            svc = TaskManagementService(db)
            msg = svc.update_task_at_index(
                owner_user_id=internal_user_id,
                task_refs=task_refs,
                index=task_index,
                task_body=(slots.get("task_body") or "").strip() or None,
                due_at_raw=(slots.get("due_at") or "").strip() or None,
            )

            if task_refs and task_index <= len(task_refs):
                new_title = (slots.get("task_body") or "").strip()
                if new_title:
                    task_refs[task_index - 1]["title"] = new_title
                    self._save_managed_task_refs(state, task_refs)
                    self.conversation_manager._save_conversation_state(state)

            logger.info(
                "Updated managed task #%s for user %s (owner=%s)",
                task_index,
                user_id,
                internal_user_id,
            )
            return IntentHandlerResult(msg, 200)
        except ValueError as e:
            return IntentHandlerResult(str(e), None)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "update_task", "error", message=detail
                ),
                None,
            )
        except Exception as e:
            logger.error("Update task failed for user %s: %s", user_id, e, exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "update_task",
                    "error",
                    message="I couldn't update that item right now. Please try again.",
                ),
                None,
            )
        finally:
            db.close()

    def _process_add_task_intent(
        self,
        user_id: str,
        slots: Dict[str, Any],
        user_data: Optional[Dict[str, Any]],
    ) -> IntentHandlerResult:
        """Create a task from collected slots (open todo, deadline reminder, or recurring reminder)."""
        from core.memory.service.reminder_delivery_service import ReminderDeliveryService
        from core.memory.service.task_intent_service import TaskIntentService

        db = self.db_session or SessionLocal()
        should_close = self.db_session is None
        try:
            internal_user_id = self._resolve_internal_user_id(user_id, user_data)
            delivery = ReminderDeliveryService.default_delivery_for_owner(user_id)
            msg = TaskIntentService(db).create_from_slots(
                owner_user_id=internal_user_id,
                slots=slots,
                delivery=delivery,
            )
            logger.info("Created task for user %s (owner=%s)", user_id, internal_user_id)
            return IntentHandlerResult(msg, 200)
        except HTTPException as e:
            detail = e.detail if isinstance(e.detail, str) else str(e.detail)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "add_task", "error", message=detail
                ),
                None,
            )
        except ValueError:
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "", "account_required", channel=channel_type(user_id)
                ),
                None,
            )
        except Exception as e:
            logger.error("Add task failed for user %s: %s", user_id, e, exc_info=True)
            return IntentHandlerResult(
                self.response_formatter.format_response(
                    "add_task",
                    "error",
                    message="I couldn't save that task. Please try again.",
                ),
                None,
            )
        finally:
            if should_close:
                db.close()

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
        db = self.db_session or SessionLocal()
        should_close = self.db_session is None
        try:
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

            user = None
            if self._telegram_context_user and channel_type(user_id) == "telegram":
                user = self._telegram_context_user
            elif channel_type(user_id) == "telegram":
                user = resolve_telegram_user(
                    db,
                    user_id,
                    None,
                    conversation_manager=self.conversation_manager,
                )
            else:
                user = user_service.find_user_by_phone(channel_user_id)

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
            if should_close:
                db.close()

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
