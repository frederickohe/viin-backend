from typing import Dict, Any

class ResponseFormatter:
    @staticmethod
    def format_response(intent: str, message_type: str, **kwargs) -> str:
        """Format responses in a friendly business assistant style"""
        
        if message_type == "missing_slots":
            if intent == "add_task":
                return kwargs.get("prompt", "I need a bit more information to save this task.")
            return f"{kwargs.get('message', 'I would need a few more details to complete the task,')} {kwargs.get('prompt', '')}"
        
        elif message_type == "confirm_action":
            action_descriptions = {
                "make_payment": f"pay GHS {kwargs.get('amount')} via Paystack",
            }
            action_desc = action_descriptions.get(intent, "complete this transaction")
            return f"🔒 Please enter your 5-digit PIN to confirm {action_desc}."
        
        elif message_type == "success":
            return f"✅ {kwargs.get('message', 'Action completed successfully!')}"
        
        elif message_type == "intent_not_clear":
            return "I'm not quite sure what you're asking. Could you please rephrase or provide more details? I can help you with: todos and reminders, email, media generation, Paystack payments, daily or weekly briefings, and profile updates."
        
        elif message_type == "account_required":
            return (
                "You need a Viin account before I can do that. "
                "Please sign up and verify your account, then sign in and try again."
            )

        elif message_type == "error":
            return kwargs.get("message") or "Something went wrong. Please try again."

        elif message_type == "ask_for_image_description":
            return "I couldn't process the image automatically. Could you please describe what's in the image, or send a short caption?"
        
        elif message_type == "invalid_pin":
            return "🔒 Invalid PIN. Please try again."

        elif message_type == "payment_confirmation":
            return kwargs.get('message', 'Please confirm this transaction.')

        elif message_type == "payment_cancelled":
            return "❌ Transaction cancelled. Your account has not been charged."

        elif message_type == "confirm_again":
            return f"❓ {kwargs.get('message', 'Please reply yes or no.')}"

        elif message_type == "processing":
            return kwargs.get('message', 'Your payment is being processed.')

        elif message_type == "transaction_in_progress":
            return "I am currently fulfilling a transaction for you. Please wait a moment, I will attend to you when I'm done."

        elif message_type == "intervention_created":
            return "I’m bringing in a human agent to help you. Please hold on a moment."

        elif message_type == "intervention_active":
            return "A human agent is currently assisting you. Please share any extra details and they’ll respond shortly."

        else:
            return f"💬 {kwargs.get('message', '')}"