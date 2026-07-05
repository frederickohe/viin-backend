from typing import Dict, List, Any, Tuple
import logging
from core.nlu.config import INTENTS, MODEL, SYSTEM_PROMPTS, VENDOR_EXCLUSION_RULES
from core.nlu.service.llmclient import LLMClient  # Add this import
from utilities.phone_utils import extract_ghana_phone_numbers_from_text, clean_ocr_text

logger = logging.getLogger(__name__)

class IntentDetector:
    def __init__(self, use_advanced_model: bool = True):
        self.intents = INTENTS
        # Use advanced model for intent extraction if specified
        model = MODEL if use_advanced_model else None
        self.llm_client = LLMClient(model=model)

    def detect_intent_and_slots(self, user_message: str, conversation_history: List[Dict], current_intent: str = None, media_context: Dict = None) -> Tuple[str, Dict, List[str]]:
        """
        Detect user intent and extract slots from message
        Returns: (intent, extracted_slots, missing_slots)
        """
        
        # Prepare conversation context
        context = self._prepare_context(conversation_history)
        
        # Use conversational system prompt for intent detection
        system_prompt = SYSTEM_PROMPTS["conversational"].format(
            context=context,
            vendor_rules=VENDOR_EXCLUSION_RULES.strip(),
        )

        # Enhanced prompt with context awareness and precision
        prompt = self._create_enhanced_prompt(user_message, current_intent)
        
        # Create prompt for intent detection
        # prompt = f"""
        # Read the user's message and extract:
        # 1. The main intent from this list: {list(self.intents.keys())}
        # 2. Any relevant information (slots) for that intent
        
        # User message: "{user_message}"
        
        # Available intents and their slots:
        # {self._format_intents_for_prompt()}
        
        # IMPORTANT RULES FOR BENEFICIARY DETECTION:
        # - For send_money and buy_airtime intents: If the user mentions a NAME (not a phone number), extract it as "customer_name" slot
        # - Examples of names: "Send to John", "Buy airtime for Mom", "Send money to Ama"
        # - If a phone number is provided directly, use it as "recipient" or "phone_number" slot
        # - Both name and number can be provided; if name is provided, prefer extracting the name as customer_name slot
        # - The system will look up the saved customer by name and extract the phone number automatically

        # IMPORTANT RULE FOR REFERENCE EXTRACTION:
        # - Extract "for [purpose]" phrases as the reference slot, WITHOUT the "for" keyword
        # - Examples: "send 2 cedis to Autobusney for food" → extract reference as "food"
        # - Examples: "send 50 to John for transport" → extract reference as "transport"
        # - Examples: "send 100 cedis to Ama for school fees" → extract reference as "school fees"
        # - The reference describes the purpose or reason for the payment
        
        # TEMPORAL AND ACTION DISTINCTION:
        # - Past tense queries with "how much", "how many", "have I", "did I send", "have I sent" → expense_report
        # - Action language with "buy", "send", "pay" in imperative form → transactional (send_money, buy_airtime, pay_bill)
        # - Query language with "check", "view", "show", "tell me my" → expense_report or informational
        # - Time references like "today", "this week", "last month" in a query context → expense_report
        
        # Respond in this exact format:
        # INTENT: [detected_intent]
        # SLOTS: [json_object_with_slots]
        # MISSING: [comma_separated_missing_slots]
        
        # Example with customer name:
        # INTENT: send_money
        # SLOTS: {{"amount": "50", "customer_name": "John", "reference": "food"}}
        # MISSING: 
        
        # Example with direct phone number:
        # INTENT: send_money
        # SLOTS: {{"amount": "50", "recipient": "0234567890"}}
        # MISSING: reference
        # """

        try:
            logger.debug("Intent detection start: user_message=%s current_intent=%s media_present=%s", user_message, current_intent, bool(media_context))

            # Initialize extracted phones list
            extracted_phones_from_image = []
            
            # If audio bytes are present, transcribe and include transcription
            if media_context and media_context.get("audio_bytes"):
                try:
                    logger.info("Transcribing audio for intent detection: filename=%s", media_context.get("audio_filename"))
                    transcription = self.llm_client.transcribe_audio_from_bytes(
                        media_context.get("audio_bytes"),
                        filename=media_context.get("audio_filename", "audio.mp3")
                    )
                    logger.info("Audio transcription result: %s", transcription)
                    if transcription:
                        user_message = user_message + f"\n{transcription}"
                except Exception as ex:
                    logger.warning("Audio transcription failed: %s", ex)

            # If image is present, extract text and include in prompt (not as image parameter)
            extracted_phones_from_image = []
            if media_context and (media_context.get("image_base64") or media_context.get("image_url")):
                try:
                    logger.info("Extracting text from image for intent detection")
                    image_base64 = media_context.get("image_base64")
                    image_url = media_context.get("image_url")
                    image_media_type = media_context.get("image_mime_type", "image/jpeg")
                    
                    extracted_text = self.llm_client.extract_text_from_image(
                        image_base64=image_base64,
                        image_url=image_url,
                        image_media_type=image_media_type
                    )
                    logger.debug("Image text extraction result (raw): %s", extracted_text)
                    
                    if extracted_text:
                        # Clean OCR text to remove noise (e.g., debug output like "Autobus_backend  |")
                        clean_text = clean_ocr_text(extracted_text)
                        logger.debug("Image text after cleaning: %s", clean_text)
                        
                        # Extract Ghana phone numbers from the OCR text BEFORE adding to message
                        extracted_phones_from_image = extract_ghana_phone_numbers_from_text(clean_text)
                        if extracted_phones_from_image:
                            logger.info(f"[INTENT_DETECTION] Extracted phones from image: {extracted_phones_from_image}")
                        
                        # Add cleaned text to user message
                        user_message = user_message + f"\n{clean_text}"
                except Exception as ex:
                    logger.warning("Image text extraction failed: %s", ex)

            logger.info("Calling LLMClient for intent detection (model=%s)", self.llm_client.model)
            
            # Create prompt with extracted phone numbers from image
            prompt = self._create_enhanced_prompt(user_message, current_intent, extracted_phones_from_image)
            
            response_text = self.llm_client.chat_completion(
                system_prompt=system_prompt,
                user_message=prompt,
                conversation_history=conversation_history,
                temperature=0.1,
                max_tokens=500
            )

            logger.debug("Intent detection response text (truncated): %s", (response_text or '')[:1000])

            # Detect if model refused or reported inability to process images
            refusal_phrases = [
                "unable to process images",
                "i'm unable to process",
                "cannot process images",
                "can't process images",
                "cannot access the image",
                "cannot view the image",
                "can't view images",
                "do not have the ability to view images",
                "i cannot process images",
                "i can't process images",
                "i'm not able to process images"
            ]
            if response_text:
                low = response_text.lower()
                if any(p in low for p in refusal_phrases) or "cannot_process_image" in low or "cannot_process_image" in (response_text or ""):
                    logger.info("Model reported it cannot process images; returning special intent")
                    return "cannot_process_image", {}, []

            # Parse the LLM response
            intent, slots, missing_slots = self._parse_response(response_text)
            return intent, slots, missing_slots
            
        except Exception as e:
            print(f"Error in intent detection: {e}")
            return "unknown", {}, []
    
    def _create_enhanced_prompt(self, user_message: str, current_intent: str = None, extracted_phones: List[str] = None) -> str:
        """Create enhanced prompt with context awareness and precision"""
        
        if extracted_phones is None:
            extracted_phones = []
        
        intent_guidelines = """
        INTENT DETECTION GUIDELINES:
        0. The moment a user send Hello, Hi, Hey or similar greeting, it should be classified as a greeting intent, regardless of any current intent. This is a clear signal of a new conversation flow.
        1. Be precise - read the exact words and phrasing in the user message
        2. If the message continues the current conversation flow, maintain the same intent
        3. Only change intent if the user clearly introduces a new topic or request
        4. For ambiguous messages, prefer the current intent if it makes contextual sense
        5. Consider conversation history when determining if this is a continuation

        HUMAN HANDOVER (INTERVENTIONS):
        - If the user asks to speak to a human / agent / support / representative, set intent to "request_intervention".
        - Examples: "talk to an agent", "human please", "I need support", "customer service", "can I speak to someone", "help me with an agent".
        - If the user says they want to continue with the bot (e.g. "never mind", "continue", "bot is fine"), set intent to "end_intervention".
        
        CRITICAL RULES:
        - If user provides additional information for current intent: KEEP SAME INTENT
        - If user corrects or modifies previous information: KEEP SAME INTENT  
        - If user asks clarifying questions about current task: KEEP SAME INTENT
        - Only switch intent for completely new, unrelated user text
        - PAYMENT OVERRIDE: If the message includes send/pay/transfer + an amount (e.g. "send 2 cedis to Anna"), ALWAYS use make_payment — never normal_conversation or business_conversation, even when continuing a chat.
        
        TIME PERIOD EXTRACTION GUIDANCE:
        When extracting the "time_period" slot, use one of these standardized codes:
        - TODAY: for "today", "current day", "right now"
        - YESTERDAY: for "yesterday", "last day"
        - WEEK_1: for "this week", "current week"
        - WEEK_LAST: for "last week", "past week", "previous week"
        - WEEK_2: for "2 weeks", "14 days", "past 2 weeks"
        - MONTH_1: for "this month", "current month"
        - MONTH_LAST: for "last month", "past month", "previous month"
        - MONTH_3: for "last 3 months", "90 days", "past 3 months", "quarter"
        - MONTH_6: for "last 6 months", "180 days", "past 6 months"
        - YEAR_1: for "last year", "12 months", "this year", "annual", "yearly"
        - ALL_TIME: for "all time", "everything", "entire history", "since creation"
        
        IMPORTANT: Prefer the standardized codes above over natural language variations.
        If the user provides a time period that doesn't exactly match, convert it to the appropriate code.
        Examples:
        - "last 3 months" → "MONTH_3"
        - "for the past week" → "WEEK_LAST"
        - "this month" → "MONTH_1"
        - "all" → "ALL_TIME"
        - "over the last 6 months" → "MONTH_6"

        TASK BRIEFING:
        - Requests for a daily task/todo summary → daily_briefing
        - Examples: "daily briefing", "what are my tasks for today", "my todos today", "what do I need to do today", "what's most urgent"
        - Questions about tasks due yesterday or missed items → daily_briefing
        - Examples: "was there something to do yesterday", "what did I miss yesterday", "anything due yesterday", "what was due last day"
        - Requests for a weekly task/todo summary → weekly_briefing
        - Examples: "weekly briefing", "tasks this week", "what's on my plate this week", "my week ahead"
        - Requests for a monthly task/todo summary → monthly_briefing
        - Examples: "monthly briefing", "monthly overview", "tasks this month", "what's due this month", "my month ahead"

        ADD TASK (slot collection — keep intent add_task until all required slots are filled):
        - User wants to create a task, todo, or reminder → add_task
        - Examples: "add a task", "remind me to call John", "create a todo", "add to my list"
        - Slots:
          • task_body — what needs to be done (required)
          • schedule_type — open | deadline | recurring (required)
          • due_at — required when schedule_type is deadline (date/time)
          • repeat_frequency — required when schedule_type is recurring (daily, weekly, monthly)
          • repeat_time — required when schedule_type is recurring (time of day, e.g. 8am)
        - If the user gives task + schedule in one message, extract all applicable slots.
        - If continuing add_task, keep intent add_task and only fill missing slots from the latest message.
        - schedule_type values: use "open" for no date, "deadline" for one-time due date, "recurring" for repeating tasks.

        PAYSTACK PAYMENTS:
        - User wants to send money, pay someone, transfer cedis, checkout, or subscribe → make_payment
        - Examples: "send 1 cedi to Anna 0207926310", "pay 50 cedis to John", "make a payment of 25 GHS"
        - Slots:
          • amount — payment amount in GHS (required)
          • recipient_name — who receives the payment (optional)
          • recipient_phone — recipient phone number (optional)
          • description — optional note about what the payment is for
          • payment_method — how the user wants to pay: "momo" / "mobile money" or "bank" (optional)
        - Phrases like "send X cedi to [name] [phone]" are make_payment, NOT conversational chat.
        - If the user mentions momo, mobile money, MTN, Vodafone, or AirtelTigo → payment_method: "momo"
        - If the user mentions bank or bank transfer → payment_method: "bank"

        REMINDER SHORTCUTS:
        - "remind me to [task] in X minutes/hours" → add_task with schedule_type=deadline and due_at="in X minutes"
        - Example: "remind me to text Anna in 2 minutes" → task_body="text Anna", schedule_type="deadline", due_at="in 2 minutes"

        DELETE TASK (by number from last briefing list):
        - User wants to remove/delete/cancel an item from their last numbered briefing → delete_task
        - Examples: "delete 1", "remove 2", "cancel 3", "delete task 1", "remove item 2"
        - Slot task_number is the number from the briefing list (1-based integer as string)
        - Only use delete_task when the user references a number to remove from a prior briefing list
        """
        
        current_intent_context = f"CURRENT_INTENT: {current_intent if current_intent else 'Intent Extraction'}"
        
        # Format extracted phones from images
        extracted_phones_info = ""
        if extracted_phones:
            extracted_phones_info = f"""
        IMPORTANT - PHONES EXTRACTED FROM IMAGE:
        The following Ghana phone number(s) were extracted from an image/document:
        {', '.join(extracted_phones)}

        If the user message mentions "this number", "that number", "the number", "send to this", etc., 
        these extracted phone numbers are the RECIPIENTS. Use them for "recipient" or "phone_number" slots.
        """
        
        return f"""
        {intent_guidelines}
        
        {current_intent_context}
        You are an expert conversational AI that identifies user intent and extracts relevant slot information.
        A slot is a specific piece of information needed to fulfill an intent (e.g., amount, recipient).

        Your goals:
        1. Identify the user's **main intent** from the list below:
        List of defined intents: {list(self.intents.keys())}
        2. Extract slot values relevant to that intent.
        3. If the message is a continuation of an existing intent (current_intent = "{current_intent}"), 
        maintain that same intent **unless** the user clearly starts a new topic.
        4. Accurately identify missing required slots for that intent.
        
        User message to read: "{user_message}"
        {extracted_phones_info}
        Available intents and their slots:
        {self._format_intents_for_prompt()}
        
        DECISION PROCESS:
        - Is this message clearly about a NEW intent? → Use new intent
        - Is this message continuing/refining the CURRENT intent? → Keep current intent
        - Is this message ambiguous but contextually related? → Prefer current intent
        
        Respond in this EXACT format:
        INTENT: [detected_intent]
        SLOTS: [json_object_with_slots]
        MISSING: [comma_separated_missing_slots]
        
        Examples:
        User starts make_payment: "Pay 50 cedis via momo"
        INTENT: make_payment
        SLOTS: {{"amount": "50", "payment_method": "momo"}}
        MISSING:

        User starts make_payment: "Send 100 cedis by bank transfer"
        INTENT: make_payment
        SLOTS: {{"amount": "100", "payment_method": "bank"}}
        MISSING:

        User starts make_payment: "Send 1 cedi to Anna 0207926310"
        INTENT: make_payment
        SLOTS: {{"amount": "1", "recipient_name": "Anna", "recipient_phone": "0207926310"}}
        MISSING:

        User starts make_payment: "Pay 50 cedis"
        INTENT: make_payment
        SLOTS: {{"amount": "50"}}
        MISSING:

        User starts make_payment: "I want to pay 100 cedis for my subscription"
        INTENT: make_payment
        SLOTS: {{"amount": "100", "description": "subscription"}}
        MISSING:

        User continues make_payment: "Actually, make it 75 cedis"
        INTENT: make_payment
        SLOTS: {{"amount": "75"}}
        MISSING:

        User starts add_task reminder: "Remind me to text Anna in 2 minutes"
        INTENT: add_task
        SLOTS: {{"task_body": "text Anna", "schedule_type": "deadline", "due_at": "in 2 minutes"}}
        MISSING:

        User starts add_task: "Add a task to buy groceries"
        INTENT: add_task
        SLOTS: {{"task_body": "buy groceries"}}
        MISSING: schedule_type

        User continues add_task: "deadline"
        INTENT: add_task
        SLOTS: {{"schedule_type": "deadline"}}
        MISSING: due_at

        User continues add_task: "tomorrow at 3pm"
        INTENT: add_task
        SLOTS: {{"due_at": "tomorrow at 3pm"}}
        MISSING:

        User starts add_task with full deadline: "Remind me to submit the report on Friday at 10am"
        INTENT: add_task
        SLOTS: {{"task_body": "submit the report", "schedule_type": "deadline", "due_at": "Friday at 10am"}}
        MISSING:

        User starts recurring task: "Remind me every day at 8am to take medicine"
        INTENT: add_task
        SLOTS: {{"task_body": "take medicine", "schedule_type": "recurring", "repeat_frequency": "daily", "repeat_time": "8am"}}
        MISSING:

        User starts open task: "Add task: call the supplier — no deadline"
        INTENT: add_task
        SLOTS: {{"task_body": "call the supplier", "schedule_type": "open"}}
        MISSING:

        User deletes from briefing list: "delete 2"
        INTENT: delete_task
        SLOTS: {{"task_number": "2"}}
        MISSING:

        User deletes from briefing list: "remove task 1"
        INTENT: delete_task
        SLOTS: {{"task_number": "1"}}
        MISSING:
        Examples end.

        Notes for accuracy:
        - Payment requests routed through Paystack use make_payment
        - If the user's message clarifies or adds to the **current intent**, do not change it.
        - Only switch intent if the message explicitly refers to a different goal or action.
        - Always ensure `SLOTS` is valid JSON.
        """
    
    def _prepare_context(self, conversation_history: List[Dict]) -> str:
        """Prepare conversation context for the AI"""
        if not conversation_history:
            return "New conversation"
        
        context = "Recent conversation:\n"
        for msg in conversation_history[-5:]:  # Last 5 messages
            context += f"{msg['role']}: {msg['content']}\n"
        return context
    
    def _format_intents_for_prompt(self) -> str:
        """Format intents for the prompt"""
        formatted = ""
        for intent, details in self.intents.items():
            formatted += f"- {intent}: {details['description']} (slots: {', '.join(details['slots'])})\n"
        return formatted
    
    def _parse_response(self, response_text: str) -> Tuple[str, Dict, List[str]]:
        """Parse the AI response into structured data"""
        intent = "unknown"
        slots = {}
        missing_slots = []
        
        if not response_text:
            return intent, slots, missing_slots
            
        lines = response_text.strip().split('\n')
        for line in lines:
            if line.startswith('INTENT:'):
                intent = line.replace('INTENT:', '').strip()
            elif line.startswith('SLOTS:'):
                import json
                try:
                    slots_str = line.replace('SLOTS:', '').strip()
                    slots = json.loads(slots_str) if slots_str else {}
                except:
                    slots = {}
            elif line.startswith('MISSING:'):
                missing_str = line.replace('MISSING:', '').strip()
                missing_slots = [s.strip() for s in missing_str.split(',')] if missing_str else []
        
        return intent, slots, missing_slots