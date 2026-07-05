from typing import Dict, List, Any, Optional
from core.nlu.config import INTENTS
from core.memory.service.task_intent_service import normalize_schedule_type

_PLACEHOLDER_ORDER_ITEM_NAMES = frozenset(
    {
        "order",
        "an order",
        "a order",
        "new order",
        "the order",
        "item",
        "items",
        "product",
        "products",
        "goods",
        "something",
        "purchase",
        "merchandise",
    }
)


def is_placeholder_order_item_name(name: str) -> bool:
    n = (name or "").strip().lower()
    if len(n) < 2:
        return True
    return n in _PLACEHOLDER_ORDER_ITEM_NAMES


_ACRONYM_SLOT_WORDS = frozenset({"id", "url", "pin", "sms", "ecg", "dst", "got"})


def format_slot_label(slot: str) -> str:
    """Turn snake_case slot keys into user-facing labels (e.g. phone_number -> Phone number)."""
    if not slot:
        return ""
    words: List[str] = []
    for part in slot.strip().split("_"):
        if not part:
            continue
        lower = part.lower()
        if lower in _ACRONYM_SLOT_WORDS:
            words.append(lower.upper())
        else:
            words.append(lower.capitalize())
    return " ".join(words)


class SlotManager:
    def __init__(self):
        self.intents = INTENTS
    
    def get_missing_slots(self, intent: str, current_slots: Dict) -> List[str]:
        """Get list of missing required slots for an intent"""
        if intent == "add_task":
            return self._missing_add_task_slots(current_slots)

        if intent not in self.intents:
            return []
        
        required_slots = self.intents[intent].get("required_slots", [])
        missing = []
        
        for slot in required_slots:
            if slot not in current_slots or not current_slots[slot]:
                missing.append(slot)
        
        return missing
    
    def validate_slots(self, intent: str, slots: Dict) -> Dict:
        """Validate and clean extracted slots"""
        validated_slots = {}

        for slot, value in slots.items():
            if value:
                # Basic validation based on slot type
                if "amount" in slot:
                    validated_slots[slot] = self._validate_amount(value)
                elif "account_number" in slot:
                    # Account numbers should not be validated - they can be in any format
                    validated_slots[slot] = str(value).strip()
                elif "email" not in slot and ("phone" in slot or "recipient" in slot or "number" in slot):
                    validated_slots[slot] = self._validate_phone_number(value)
                else:
                    validated_slots[slot] = str(value).strip()

        if intent == "create_order":
            iname = validated_slots.get("item_name")
            if iname and is_placeholder_order_item_name(str(iname)):
                validated_slots.pop("item_name", None)

        if intent == "add_task":
            schedule = normalize_schedule_type(validated_slots.get("schedule_type"))
            if schedule:
                validated_slots["schedule_type"] = schedule
            freq = (validated_slots.get("repeat_frequency") or "").strip().lower()
            if freq in ("daily", "day"):
                validated_slots["repeat_frequency"] = "daily"
            elif freq in ("weekly", "week"):
                validated_slots["repeat_frequency"] = "weekly"
            elif freq in ("monthly", "month"):
                validated_slots["repeat_frequency"] = "monthly"

        return validated_slots
    
    def _validate_amount(self, amount: str) -> Optional[str]:
        """Validate amount format"""
        try:
            # Remove currency symbols and commas
            clean_amount = ''.join(c for c in str(amount) if c.isdigit() or c == '.')
            if clean_amount:
                return str(float(clean_amount))
        except:
            pass
        return None
    
    def _validate_phone_number(self, phone: str) -> Optional[str]:
        """Validate Ghana phone number format"""
        # Remove spaces, dashes, etc.
        clean_phone = ''.join(c for c in str(phone) if c.isdigit())
        
        # Ghana numbers: 10 digits starting with 0, or 9 digits without 0
        if len(clean_phone) == 10 and clean_phone.startswith('0'):
            return clean_phone
        elif len(clean_phone) == 9:
            return f"0{clean_phone}"
        
        return None
    
    def _quantity_prompt(self, intent: str) -> str:
        if intent in ("add_product", "update_product"):
            return "How many units are you adding?"
        if intent in ("create_order", "update_order"):
            return "How many units should be ordered?"
        return "How many units?"

    def _slot_description(self, intent: str, slot: str, bill_providers: Dict[str, str]) -> str:
        if slot == "quantity":
            return self._quantity_prompt(intent)
        if slot == "description" and intent == "make_payment":
            return "What is this payment for? (optional)"

        slot_descriptions = {
            "amount": "How much would you like to pay (in GHS)?",
            "category": "Which category?",
            "period": "For what period?",
            "time_period": "For what time period?",
            "recipient_email": "What's the recipient's email address?",
            "sender_email": "What sender email address should we use when you send mail?",
            "subject": "What's the subject of the email?",
            "body": "What's the body/message of the email?",
            "item_name": "What product or item is being ordered?",
            "order_number": "Which order number should I invoice (e.g. ORD-20260318-12345)?",
            "order_id": "Which order ID should I invoice?",
            "product_name": "What is the product name?",
            "product_id": "Which product (ID or name)?",
            "price": "What is the price?",
            "condition": "What is the product condition? (e.g. new, used)",
            "description": "What is the product description?",
            "photo": "Please send a photo of the product (you can send multiple images).",
            "photos": "Send more product photos, or say done when finished.",
            "update_field": "What would you like to update? (name, number)",
            "task_body": "What is the task? Describe what you need to do.",
            "schedule_type": (
                "Should this task have a deadline, repeat on a schedule, or stay open with no date?\n"
                "Reply with one of:\n"
                "• open — no deadline\n"
                "• deadline — due once at a specific date/time\n"
                "• recurring — repeats daily, weekly, or monthly at a set time"
            ),
            "due_at": "When is it due? Give a date and time (e.g. tomorrow at 3pm, or 2026-07-10 14:00).",
            "repeat_frequency": "How often should it repeat? (daily, weekly, or monthly)",
            "repeat_time": "What time should it repeat each cycle? (e.g. 8am, 5:30pm)",
        }

        if slot in slot_descriptions:
            return slot_descriptions[slot]
        label = format_slot_label(slot)
        return f"What is the {label.lower()}?"

    def _missing_add_task_slots(self, current_slots: Dict) -> List[str]:
        """Collect task slots one step at a time based on schedule choice."""
        if not (current_slots.get("task_body") or "").strip():
            return ["task_body"]

        schedule = normalize_schedule_type(current_slots.get("schedule_type"))
        if not schedule:
            return ["schedule_type"]

        if schedule == "deadline":
            if not (current_slots.get("due_at") or "").strip():
                return ["due_at"]
            return []

        if schedule == "recurring":
            if not (current_slots.get("repeat_frequency") or "").strip():
                return ["repeat_frequency"]
            if not (current_slots.get("repeat_time") or "").strip():
                return ["repeat_time"]
            return []

        if schedule == "open":
            return []

        return ["schedule_type"]

    def generate_slot_prompt(self, intent: str, missing_slots: List[str]) -> str:
        """Generate natural language prompt for missing slots with intent-aware context"""

        if not missing_slots:
            return "can you be more detailed about your request?"

        bill_providers = {
            "GoTV": "GOT",
            "DStv": "DST",
            "ECG": "ECG",
            "Ghana Water": "GHW",
            "Surfline": "SFL",
            "Telesol": "TLS",
            "Startimes": "STT",
            "Box Office": "BXO",
        }

        if len(missing_slots) == 1:
            return self._slot_description(intent, missing_slots[0], bill_providers)

        lines = [f"• {format_slot_label(slot)}" for slot in missing_slots]
        return "I still need the following:\n" + "\n".join(lines)

    def _generate_bill_type_prompt(self, bill_providers: Dict[str, str]) -> str:
        """Generate prompt with list of available bill providers on separate lines"""
        providers_list = "\n".join([f"• {name} ({code})" for name, code in bill_providers.items()])
        return f"Which bill would you like to pay?\nAvailable options:\n{providers_list}"
