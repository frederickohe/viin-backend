# core/nlu/service/intent_processor.py
import json
import re
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Any, Optional
from core.nlu.service.llmclient import LLMClient
from core.nlu.config import SYSTEM_PROMPTS, RESPONSE_TEMPLATES, VENDOR_EXCLUSION_RULES
from config import settings
from core.nlu.service.datapipe.dataconfig import FINANCIAL_INSIGHTS_SYSTEM_PROMPT, INSIGHTS_SYSTEM_PROMPT
from core.nlu.service.datapipe.user_rag import UserRAGManager
from core.user.controller.usercontroller import get_db
from utilities.phone_utils import normalize_ghana_phone_number
import logging
from fastapi import HTTPException
from core.nlu.service.datapipe.dataengine import EnhancedUserRAGManager

from core.agent.tools.agent_config.user_agent_config_service import AgentConfigService

logger = logging.getLogger(__name__)

_MOMO_PAYSTACK_CHANNELS = ["mobile_money"]
_BANK_PAYSTACK_CHANNELS = ["bank"]


def _resolve_paystack_channels(payment_method: Optional[str]) -> Optional[List[str]]:
    method = (payment_method or "").strip().lower()
    if method == "momo":
        return _MOMO_PAYSTACK_CHANNELS
    if method == "bank":
        return _BANK_PAYSTACK_CHANNELS
    return None


def _payment_method_label(payment_method: Optional[str]) -> str:
    method = (payment_method or "").strip().lower()
    if method == "momo":
        return " (Mobile Money)"
    if method == "bank":
        return " (Bank transfer)"
    return ""

class IntentProcessor:
    """Processes intents using LLM and agent framework tools"""
    
    def __init__(self, db_session=None):
        self.llm_client = LLMClient()
        self.rag_manager = UserRAGManager()  # Initialize RAG manager
        self.db_session = db_session
        self._email_tool = None

    @property
    def email_tool(self):
        """Lazy-init: avoids Redis/DB setup unless an email intent runs."""
        if self._email_tool is None:
            from core.agent.tools.email.email import EmailTool

            self._email_tool = EmailTool()
        return self._email_tool
    
    def process_conversational_intent(
        self, 
        intent: str, 
        user_message: str, 
        conversation_history: List[Dict],
        slots: Dict[str, Any],
        user_id: str = None,
        user_data: Optional[Dict] = None,
        task_context: Optional[str] = None,
    ) -> str:
        """
        Process conversational intents with optional Postgres task-memory context.

        Args:
            intent: Intent type
            user_message: User's message
            conversation_history: Conversation history
            slots: Extracted slots
            user_id: User ID (for logging / future personalization hooks)
            user_data: Additional user data
            task_context: Pending reminders, todos, and notes from task memory

        Returns:
            Generated response
        """
        if intent == "greeting":
            return self._build_greeting_response(user_data)

        if intent == "goodbye" and self._is_customer_session(user_data):
            return RESPONSE_TEMPLATES["conversational"]["customer_goodbye"]

        prompt_key = (
            "customer_conversational"
            if self._is_customer_session(user_data)
            else "conversational"
        )

        # Prepare enhanced system prompt with user context
        system_prompt = self._build_enhanced_system_prompt(
            base_prompt=SYSTEM_PROMPTS[prompt_key],
            user_data=user_data,
            intent=intent,
            slots=slots
        )

        if task_context and task_context.strip():
            system_prompt = (
                system_prompt
                + "\n\n## User task memory (from database)\n"
                + task_context.strip()
            )
        
        response = self.llm_client.chat_completion(
            system_prompt=system_prompt,
            user_message=user_message,
            conversation_history=conversation_history,
            temperature=0.7
        )
        
        return self._format_conversational_response(intent, response, slots)

    def process_payment_intent(
        self,
        intent: str,
        slots: Dict[str, Any],
        user_data: Optional[Dict] = None,
    ) -> str:
        """Initialize a Paystack checkout for the user."""
        from core.paystack.dto.request.paystack_request import PaystackInitializeRequest
        from core.paystack.service.paystack_customer import resolve_paystack_customer_email
        from core.paystack.service.paystack_errors import format_paystack_user_message
        from core.paystack.service.paystack_service import PaystackService
        from core.user.model.User import User

        user_id = (user_data or {}).get("db_user_id") or (user_data or {}).get("user_id")
        if not user_id:
            return RESPONSE_TEMPLATES["payment"]["error"]

        db = self.db_session
        should_close = False
        if db is None:
            from utilities.dbconfig import SessionLocal
            db = SessionLocal()
            should_close = True

        from core.user.service.user_service import UserService

        user = db.query(User).filter(User.id == str(user_id)).first()
        if not user:
            phone = str(
                (user_data or {}).get("customer_phone")
                or (user_data or {}).get("user_id")
                or ""
            ).strip()
            if phone:
                user = UserService(db).find_user_by_phone(phone)
                if user:
                    user_id = user.id
        email = resolve_paystack_customer_email(user=user, user_data=user_data)

        try:
            amount_ghs = float(slots.get("amount", 0))
        except (TypeError, ValueError):
            if should_close:
                db.close()
            return "Please provide a valid payment amount in GHS."

        if amount_ghs <= 0:
            if should_close:
                db.close()
            return "Please provide a payment amount greater than zero."

        amount_pesewas = int(round(amount_ghs * 100))
        recipient_name = (slots.get("recipient_name") or slots.get("recipient") or "").strip()
        recipient_phone = (slots.get("recipient_phone") or slots.get("phone_number") or "").strip()
        description = (slots.get("description") or "").strip()
        payer_name = (user_data or {}).get("fullname", "").strip()
        payer_phone = (user_data or {}).get("customer_phone") or (user_data or {}).get("user_id") or ""
        payer_phone = str(payer_phone).strip()

        if not description and (recipient_name or recipient_phone):
            parts = []
            if recipient_name:
                parts.append(recipient_name)
            if recipient_phone:
                parts.append(recipient_phone)
            description = f"Payment to {' '.join(parts)}"

        metadata = {"intent": "make_payment"}
        if description:
            metadata["description"] = description
        if recipient_name:
            metadata["recipient_name"] = recipient_name
        if recipient_phone:
            metadata["recipient_phone"] = recipient_phone
        if payer_name:
            metadata["payer_name"] = payer_name
        if payer_phone:
            metadata["payer_phone"] = payer_phone
        callback_url = (settings.PAYSTACK_BILLING_CALLBACK_URL or "").strip() or None

        paystack_service = PaystackService(db)
        request = PaystackInitializeRequest(
            email=email,
            amount=amount_pesewas,
            metadata=metadata,
            callback_url=callback_url,
            channels=None,
        )

        try:
            result = paystack_service.initialize_transaction_sync(
                user_id=str(user_id),
                request=request,
            )
        except HTTPException as exc:
            logger.error("Paystack initialize failed for user %s: %s", user_id, exc.detail)
            return format_paystack_user_message(exc.detail)
        except Exception as exc:
            logger.exception("Paystack initialize failed for user %s: %s", user_id, exc)
            return RESPONSE_TEMPLATES["payment"]["error"]
        finally:
            if should_close:
                db.close()

        if not result.authorization_url:
            return RESPONSE_TEMPLATES["payment"]["error"]

        template = RESPONSE_TEMPLATES["payment"]["make_payment"]
        recipient_label = ""
        if recipient_name or recipient_phone:
            label_parts = [p for p in (recipient_name, recipient_phone) if p]
            recipient_label = f" to {' '.join(label_parts)}"
        return template.format(
            amount=f"{amount_ghs:.2f}",
            recipient_label=recipient_label,
            payment_url=result.authorization_url,
            reference=result.reference or "",
        )

    def process_expense_report_intent(
        self,
        intent: str,
        user_message: str,
        conversation_history: List[Dict],
        slots: Dict[str, Any],
        user_data: Optional[Dict] = None
    ) -> str:
        """
        Process expense report with enhanced financial insights
        """
        
        # Build enhanced system prompt
        system_prompt = self._build_enhanced_system_prompt(
            base_prompt=SYSTEM_PROMPTS["expense_report"],
            user_data=user_data,
            intent=intent,
            slots=slots
        )
        
        response = self.llm_client.chat_completion(
            system_prompt=system_prompt,
            user_message=user_message,
            conversation_history=conversation_history,
            temperature=0.4
        )
        
        return self._clean_markdown_formatting(response)
    
    def _build_enhanced_system_prompt(
        self,
        base_prompt: str,
        user_data: Optional[Dict],
        intent: str,
        slots: Dict
    ) -> str:
        """
        Build enhanced system prompt with user context RAG
        """
        conversational_intents = {
            "greeting",
            "normal_conversation",
            "business_conversation",
            "small_talk",
            "goodbye",
        }

        # Organization profile for chat (tenant-scoped, not platform vendor).
        user_context_section = ""
        if user_data and intent in conversational_intents:
            user_context_section = self._format_organization_context(user_data)
        elif user_data and intent == "expense_report":
            # user_data produced by NLU uses the key 'user_id' (not 'id')
            # Ensure we pass a string user_id to the RAG manager so it matches
            # the History.user_id column (which is stored as string).
            # Get user name
            user_name = f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip()
            if not user_name:
                user_name = user_data.get('username', 'User')
            
            # Get time frame from slots or default
            time_frame = slots.get('time_period', 'the selected period')
            
            # Fetch transactions using your existing method
            transactions = self.rag_manager.get_transaction_history(
                user_id=user_data.get('user_id'),
                intent=intent,
                slots=slots
            )
            
            rag_manager = EnhancedUserRAGManager()
            
            user_financial_context = rag_manager.get_financial_insights_context(
                user_name=user_name,
                user_id=user_data.get('user_id'),
                transactions=transactions,
                time_frame=time_frame,
                user_phone=user_data.get('phone_number')
            )
            user_context_section = f"User Transaction Data:\n{json.dumps(user_financial_context, indent=2)}"
            print(f"[ENHANCED_SYSTEM_PROMPT] User Transaction Data for {user_name}:\n{json.dumps(user_financial_context, indent=2)}")
        format_kwargs: Dict[str, Any] = {
            "context": user_context_section or "No organization profile on file.",
            "missing_slots": "",
            "category": slots.get("category", "general"),
        }
        if "{vendor_rules}" in base_prompt:
            format_kwargs["vendor_rules"] = VENDOR_EXCLUSION_RULES.strip()

        enhanced_prompt = base_prompt.format(**format_kwargs)

        return enhanced_prompt

    @staticmethod
    def _format_organization_context(user_data: Dict[str, Any]) -> str:
        company = (user_data.get("company") or "").strip()
        workplace = (user_data.get("organization_workplace") or "").strip()
        fullname = (user_data.get("fullname") or "").strip()
        email = (user_data.get("email") or "").strip()
        lines = []
        if company:
            lines.append(f"Business name: {company}")
        if workplace:
            lines.append(f"Organization / workplace: {workplace}")
        if fullname:
            lines.append(f"Account holder: {fullname}")
        if email:
            lines.append(f"Contact email: {email}")
        if not lines:
            return "No organization profile on file."
        return "\n".join(lines)

    @staticmethod
    def _is_customer_session(user_data: Optional[Dict[str, Any]]) -> bool:
        return bool((user_data or {}).get("is_customer_session"))

    @staticmethod
    def _business_display_name(user_data: Optional[Dict[str, Any]]) -> Optional[str]:
        if not user_data:
            return None
        for key in ("company", "organization_workplace", "fullname"):
            val = (user_data.get(key) or "").strip()
            if val:
                return val
        merchant_id = (user_data.get("merchant_id") or user_data.get("db_user_id") or "").strip()
        return merchant_id or None

    @staticmethod
    def _greeting_display_name(user_data: Optional[Dict[str, Any]]) -> Optional[str]:
        """Prefer fullname, then email local-part, for a short personalized greeting."""
        if not user_data:
            return None
        name = (user_data.get("fullname") or "").strip()
        if name:
            return name
        email = (user_data.get("email") or "").strip()
        if "@" in email:
            local = email.split("@", 1)[0].strip()
            if local:
                return local
        return None

    def _build_greeting_response(self, user_data: Optional[Dict[str, Any]]) -> str:
        templates = RESPONSE_TEMPLATES["conversational"]
        if self._is_customer_session(user_data):
            business = self._business_display_name(user_data)
            if business:
                return templates["customer_greeting_named"].replace("{business}", business)
            return templates["customer_greeting_anonymous"]
        display_name = self._greeting_display_name(user_data)
        if display_name:
            return templates["greeting_named"].replace("{name}", display_name)
        return templates["greeting_anonymous"]

    def _format_conversational_response(self, intent: str, response: str, slots: Dict) -> str:
        """Format conversational responses using templates"""
        template_data = RESPONSE_TEMPLATES["conversational"]
        
        if intent in template_data:
            template = template_data[intent]
            return template.format(response=response, **slots)
        
        return response

    def _clean_markdown_formatting(self, response: str) -> str:
        """
        Remove markdown formatting from response.
        Removes bold (**text**), italic (*text*), and other common markdown symbols
        """
        import re
        
        # Remove bold (**text** or __text__)
        response = re.sub(r'\*\*(.+?)\*\*', r'\1', response)
        response = re.sub(r'__(.+?)__', r'\1', response)
        
        # Remove italic (*text* or _text_) - be careful not to remove single asterisks
        response = re.sub(r'\*([^*\n]+)\*', r'\1', response)
        response = re.sub(r'_([^_\n]+)_', r'\1', response)
        
        # Remove markdown headings (# ## ### etc)
        response = re.sub(r'^#+\s+', '', response, flags=re.MULTILINE)
        
        # Remove markdown code blocks (```code```)
        response = re.sub(r'```.*?```', '', response, flags=re.DOTALL)
        
        # Remove inline code (`code`)
        response = re.sub(r'`([^`]+)`', r'\1', response)
        
        return response.strip()

    # ===== EMAIL INTENT HANDLER =====
    def process_email_intent(
        self,
        intent: str,
        user_message: str,
        conversation_history: List[Dict],
        slots: Dict[str, Any],
        user_id: str,
        agent_name: str = "email_agent",
        user_data: Optional[Dict] = None
    ) -> str:
        """
        Process email intents using EmailTool
        
        Supported intents:
        - send_email: Send an email to a recipient
        - read_emails: Read recent emails from inbox
        - update_sender_email: Configure outbound sender address
        """
        try:
            if intent == "send_email":
                return self._handle_send_email(user_id, slots, agent_name)
            elif intent == "read_emails":
                return self._handle_read_emails(user_id, slots)
            elif intent == "update_sender_email":
                return self._handle_update_sender_email(user_id, slots, agent_name)
            else:
                return f"❌ Email intent '{intent}' not supported"
        except Exception as e:
            logger.error(f"Error processing email intent: {e}", exc_info=True)
            return f"❌ Error processing email: {str(e)[:100]}"

    def _handle_send_email(self, user_id: str, slots: Dict[str, Any], agent_name: str) -> str:
        """Handle send_email intent using EmailTool"""
        recipient_email = slots.get("recipient_email")
        subject = slots.get("subject")
        body = slots.get("body")
        
        if not recipient_email or not subject or not body:
            missing = []
            if not recipient_email:
                missing.append("recipient email")
            if not subject:
                missing.append("subject")
            if not body:
                missing.append("body")
            return f"❌ Missing required fields: {', '.join(missing)}"
        
        # Use EmailTool to send email
        result = self.email_tool._run(
            to_email=recipient_email,
            subject=subject,
            body=body,
            user_id=user_id,
            agent_name=agent_name
        )
        
        return result

    def _handle_read_emails(self, user_id: str, slots: Dict[str, Any]) -> str:
        """List emails this user sent via EmailTool (stored when send succeeds)."""
        raw_n = slots.get("num_emails", 10)
        try:
            limit = int(float(raw_n))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(50, limit))

        rows = self.email_tool.list_sent_emails_for_user(user_id, limit=limit)
        if not rows:
            return (
                "📧 No sent emails on record yet. After you send mail through this assistant, "
                f"your last up to {limit} messages will appear here."
            )

        lines: List[str] = []
        for i, row in enumerate(rows, start=1):
            to_addr = row.get("to", "?")
            subj = row.get("subject", "(no subject)")
            sent = row.get("sent_at", "")
            lines.append(f"{i}. To: {to_addr} — {subj}\n   Sent: {sent}")
        return "📧 Your recent sent emails:\n" + "\n".join(lines)

    def _handle_update_sender_email(
        self, user_id: str, slots: Dict[str, Any], agent_name: str = "email_agent"
    ) -> str:
        """Persist the user's outbound sender email on their email_agent config."""
        sender_email = (slots.get("sender_email") or "").strip()
        if not sender_email:
            return "❌ Please provide the sender email address to use."

        if "@" not in sender_email or "." not in sender_email.split("@")[-1]:
            return "❌ That does not look like a valid email address. Please try again."

        db = next(get_db())
        try:
            service = AgentConfigService(db)
            result = service.create_or_update_agent(
                user_id=user_id,
                agent_name=agent_name,
                params={"sender_email": sender_email},
            )
            if not result.get("ok"):
                return f"❌ Could not update sender email: {result.get('message', 'unknown error')}"

            return (
                f"✅ Your sender email is now set to {sender_email}. "
                "Outgoing messages will use this address."
            )
        finally:
            db.close()

