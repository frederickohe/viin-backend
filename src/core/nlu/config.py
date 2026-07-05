import os
from typing import Dict, List, Any
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# API Configuration
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable is not set")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
MODEL = os.getenv("LLM_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
AUDIO_TRANSCRIPTION_MODEL = os.getenv("AUDIO_TRANSCRIPTION_MODEL", "whisper-large-v3")

# Local Model Configuration
MODEL_CONFIG = {
    "model_name": "microsoft/DialoGPT-large",  # or "google/flan-t5-base"
    "local_files_only": False,  # Set to True after first download
    "device": "cpu",  # or "cuda" if you have GPU
    "max_length": 512,
    "temperature": 0.1,
    "do_sample": True
}

# Intent Configuration
INTENTS = {
    # ===== CHATBOT AGENT INTENTS =====
    "greeting": {
        "description": "Greet the user",
        "slots": [],
        "required_slots": [],
        "category": "conversational"
    },
    "normal_conversation": {
        "description": "Handle general conversations",
        "slots": ["category", "user_query"],
        "required_slots": [],
        "category": "conversational"
    },
    "business_conversation": {
        "description": "Handle business-related conversations",
        "slots": ["category", "user_query"],
        "required_slots": [],
        "category": "conversational"
    },
    "goodbye": {
        "description": "End conversation politely",
        "slots": [],
        "required_slots": [],
        "category": "conversational"
    },
    
    # ===== EMAIL AGENT INTENTS =====
    "send_email": {
        "description": "Send an email to a recipient",
        "slots": ["recipient_email", "subject", "body"],
        "required_slots": ["recipient_email", "subject", "body"],
        "category": "email"
    },
    "read_emails": {
        "description": "Read recent emails from inbox",
        "slots": ["num_emails"],
        "required_slots": [],
        "category": "email"
    },
    "update_sender_email": {
        "description": "Set or update the sender email address used when sending emails",
        "slots": ["sender_email"],
        "required_slots": ["sender_email"],
        "category": "email"
    },

    #==== IMAGE GENERATION AGENT INTENTS =====
    "generate_image": {
        "description": "Generate an image based on a text prompt",
        "slots": ["prompt", "num_images"],
        "required_slots": ["prompt"],
        "category": "image_generation"
    },
    #==== VIDEO GENERATION AGENT INTENTS =====
    "generate_video": {
        "description": "Generate a video based on a text prompt",
        "slots": ["prompt", "duration"],
        "required_slots": ["prompt"],
        "category": "video_generation"
    },
    # ===== PAYSTACK PAYMENT INTENTS =====
    "make_payment": {
        "description": "Send money or pay someone via secure Paystack checkout",
        "slots": ["amount", "recipient_name", "recipient_phone", "description"],
        "required_slots": ["amount"],
        "category": "payment"
    },

    # ===== USER PROFILE MANAGEMENT INTENTS =====
    "update_username": {
        "description": "Update user username",
        "slots": ["new_username"],
        "required_slots": ["new_username"],
        "category": "user_management"
    },
    "update_phone_number": {
        "description": "Update user phone number",
        "slots": ["phone_number"],
        "required_slots": ["phone_number"],
        "category": "user_management"
    },
    "view_user_profile": {
        "description": "View current user profile information",
        "slots": [],
        "required_slots": [],
        "category": "user_management"
    },
    "update_user_details": {
        "description": "Update user profile fields such as name, phone, location, or occupation",
        "slots": ["fullname", "phone_number", "location", "occupation"],
        "required_slots": [],
        "category": "user_management"
    },

    # ===== TASK / BRIEFING INTENTS =====
    "daily_briefing": {
        "description": "Give a daily briefing of the user's todos and reminders, including overdue items and tasks due on a specific day such as yesterday",
        "slots": [],
        "required_slots": [],
        "category": "task_management"
    },
    "weekly_briefing": {
        "description": "Give a weekly briefing of the user's todos and reminders for the next 7 days, listing the most pressing items first",
        "slots": [],
        "required_slots": [],
        "category": "task_management"
    },
    "add_task": {
        "description": "Add a new task, todo, or reminder for the user",
        "slots": [
            "task_body",
            "schedule_type",
            "due_at",
            "repeat_frequency",
            "repeat_time",
        ],
        "required_slots": ["task_body", "schedule_type"],
        "category": "task_management"
    },
    
    # ===== SYSTEM INTENTS =====
    "intent_not_clear": {
        "description": "Intent could not be determined from the message",
        "slots": [],
        "required_slots": [],
        "category": "system"
    }
    
}

# Shown in chat prompts: never answer as the software vendor/platform.
VENDOR_EXCLUSION_RULES = """
- You represent the customer's organization (see Organization context), not the software platform or its vendor.
- Never describe Autobus, Greenbrain, or any underlying platform unless that exact information appears in Organization context.
- For questions about "your company", "we", or "our business", use only Organization context. If it does not contain the answer, say you do not have that information yet.
"""

# Enhanced System Prompts by Category
SYSTEM_PROMPTS = {
    "conversational": """
    You are an advanced AI task assistant for the customer's organization.
    Help users accomplish concrete goals through clear, efficient conversation — across business
    operations, financial management, and day-to-day work for that organization only.

    ## How you work
    - Treat every message as a task or step toward a goal. Identify what the user wants done, not just what they said.
    - For simple requests: answer directly and move on.
    - For multi-step or ambiguous requests: briefly confirm the goal, break work into clear steps, and ask only the questions needed to proceed.
    - Carry context across the conversation: remember what was already decided, completed, or still pending.
    - When a task needs a capability outside this chat (e.g. sending email, generating media, payments), explain what is needed and guide the user to phrase the request so the system can route it — do not pretend you already performed external actions.
    - Prefer actionable replies: what you understood, what you recommend or did in chat, and the single best next step when helpful.
    - When users ask for a daily or weekly briefing of their todos, guide them to ask explicitly (e.g. "give me my daily briefing") so the system can list pending items with the most pressing first.
    - When users want to add a task, todo, or reminder, guide them to say so explicitly (e.g. "add a task", "remind me to…") so the system can collect schedule details.

    ## Capabilities (when relevant)
    Conversational assistance, email, media generation, daily/weekly to-do briefings,
    secure Paystack payments, and profile updates — always scoped to the tenant organization.

    ## Response style
    - Be warm, professional, and direct.
    - Keep replies concise unless the user asks for detail or the task requires a structured plan.
    - Use short lists or numbered steps when breaking down work.
    {vendor_rules}

    Organization context:
    {context}
    """,

    "customer_conversational": """
    You are a friendly customer-support assistant for the business below.
    You are speaking with a customer (not the business owner). Help them with questions about
    products, services, pricing, hours, policies, and general inquiries about this business only.

    CRUCIAL RESPONSE GUIDELINES:
    - Be warm, helpful, and concise.
    - Answer as the business speaking to its customer (use "we" / "our" for the business).
    - Never offer merchant admin tasks (orders dashboard, adding products, expense reports, etc.).
    {vendor_rules}

    Business you represent:
    {context}
    """,
    
    "expense_report": """
    You are a financial assistant for the customer's organization in Ghana. You help with generating expense reports using the date_send field in the transactions data.
    Focus on:
    - Providing insights on spending patterns
    - Summarizing expenses over specified time periods

    The following section includes the user's spending data.
    If there is no transactions data available, return with a message indicating no data generated yet for that scenario.

    User Transaction Data: {context}
    Expense report criteria: {category}

    Notes for accuracy:
        - If specific time periods are mentioned, focus on those.
        - Keep response very short and concise.
        - There is field called 'status' who's value indicates if a transaction was successful or failed, thus any text containing "Failed" or "Success".
        - The customer_name field indicates the name of the recipient for a transaction, if available.
        - Strictly use the date_paid field for any time-based analysis and reporting.
    """,

    "payment": """
    You are a payment assistant for the customer's organization in Ghana.
    All money transfers and payments go through secure Paystack checkout — never promise direct mobile-money sends.
    When users say "send X cedi to [name/phone]", route them through Paystack payment, not open-ended chat confirmation.

    Current User Context: {context}
    Missing slots: {missing_slots}
    """,

    "marketing": """
    You are an expert digital marketing copywriter for small businesses in Ghana and Africa.
    Write compelling, ready-to-publish marketing text based on the user's brief.

    Guidelines:
    - Match the tone implied by the brief (professional, playful, urgent, etc.).
    - Include a clear hook, value proposition, and call to action when appropriate.
    - Keep copy concise and suitable for social media or SMS unless the user asks for longer form.
    - Do not invent specific prices, phone numbers, or URLs unless the user provided them.
    - Return only the marketing copy — no preamble, labels, or meta commentary.
    """,
}

# Enhanced Response Templates
RESPONSE_TEMPLATES = {
    "conversational": {
        # Greeting copy is resolved in IntentProcessor (name vs anonymous); keys are templates only.
        "greeting_named": "Welcome back, {name}! What would you like to get done today?",
        "greeting_anonymous": "Welcome back! What would you like to get done today?",
        "customer_greeting_named": "Hi! Welcome to {business}. How can we help you today?",
        "customer_greeting_anonymous": "Hi! How can we help you today?",
        "customer_goodbye": "Thanks for reaching out! Feel free to message us anytime.",
        "normal_conversation": "{response}",
        "business_conversation": "{response}",
        "small_talk": "{response}",
        "goodbye": "All set for now. Come back anytime you have a task to work through."
    },
    
    "email": {
        "send_email": "Your email to {recipient_email} with subject '{subject}' has been sent successfully! ✅",
        "read_emails": "Here are your {num_emails} most recent emails: {email_list}",
        "update_sender_email": "Your sender email has been set to {sender_email}. ✅",
        "missing_slots_send_email": "I'd be happy to send an email for you. Please provide: {missing_slots}",
        "error": "I apologize, but I encountered an error processing your email request. Please try again."
    },
    
    "image_generation": {
        "generate_image": "🖼️ I've successfully generated {num_images} image(s) based on your prompt: '{prompt}'. {download_info}",
        "missing_slots_generate_image": "I'd love to generate an image for you! Please provide: {missing_slots}",
        "error": "I apologize, but I couldn't generate the image. Please try again with a different prompt."
    },
    
    "video_generation": {
        "generate_video": "🎬 I've successfully generated a video based on your prompt: '{prompt}'. Duration: {duration}. {download_info}",
        "missing_slots_generate_video": "I'd love to generate a video for you! Please provide: {missing_slots}",
        "error": "I apologize, but I couldn't generate the video. Please try again with a different prompt."
    },
    
    "payment": {
        "make_payment": "💳 Pay GHS {amount}{recipient_label} via Paystack: {payment_url}\nReference: {reference}",
        "missing_slots_make_payment": "I can set up a Paystack payment for you. Please provide: {missing_slots}",
        "error": "I couldn't start the Paystack checkout right now. Please try again in a moment."
    },

    "expense_report": {
        "success": "Your expense report has been generated successfully! Here are the details: {details}",
        "error": "I apologize, but I couldn't generate the expense report. Please try again."
    },

    "system": {
        "intent_not_clear": "I want to help you get this done — could you tell me a bit more about what you're trying to accomplish? I can assist with tasks like adding todos or reminders, email, media generation, Paystack payments, daily or weekly to-do briefings, and profile updates."
    },
    "task_management": {
        "daily_briefing": "{response}",
        "weekly_briefing": "{response}",
        "add_task": "{response}",
        "error": "I couldn't complete that task action right now. Please try again in a moment."
    },

    "user_management": {
        "update_user_details": "Your profile information has been updated successfully.",
        "update_username": "Your username has been updated to '{new_username}' successfully! ✅",
        "update_phone_number": "Your phone number has been updated to '{phone_number}' successfully! ✅",
        "view_user_profile": "Here's your current profile information: {profile_details}",
        "missing_slots_username": "I'd be happy to help you update your username. Please provide your new username.",
        "missing_slots_phone": "I'd be happy to help you update your phone number. Please provide your new phone number.",
        "missing_slots": "I'd be happy to help you update your profile. Please provide the {missing_slots} you'd like to update."
    }
}

# Intent Categories for routing
INTENT_CATEGORIES = {
    "conversational": ["greeting", "normal_conversation", "business_conversation", "small_talk", "goodbye"],
    "email": ["send_email", "read_emails", "update_sender_email"],
    "image_generation": ["generate_image"],
    "video_generation": ["generate_video"],
    "payment": ["make_payment"],
    "expense_report": ["expense_report", "generate_expense_report", "monthly_expense_summary",  "annual_expense_report", "daily_expense_report","transaction_info"],
    "user_management": ["update_user_details", "update_username", "update_phone_number", "view_user_profile"],
    "task_management": ["daily_briefing", "weekly_briefing", "add_task"],
    "system": ["intent_not_clear"]
}
