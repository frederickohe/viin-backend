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
    #==== PRODUCT MANAGEMENT AGENT INTENTS =====
    "add_product": {
        "description": "Add a new product to the inventory",
        "slots": ["product_name", "price", "quantity","condition","category","description","photo","photos","link"],
        "required_slots": ["product_name", "price", "quantity","condition","category","description"],
        "category": "product_management"
    },
    "update_product": {
        "description": "Update details of an existing product",
        "slots": ["product_id", "product_name", "price", "quantity","condition","category","description","photo","photos","link"],
        "required_slots": ["product_id", "product_name", "price", "quantity","condition","category","description"],
        "category": "product_management"
    },
    "delete_product": {
        "description": "Delete a product from the inventory",
        "slots": ["product_id"],
        "required_slots": ["product_id"],
        "category": "product_management"
    },
    "view_products": {
        "description": "View all products in a products inventory",
        "slots": [],
        "required_slots": [],
        "category": "product_management"
    },
    "view_product": {
        "description": "View details of a specific product",
        "slots": ["product_id"],
        "required_slots": ["product_id"],
        "category": "product_management"
    },
    #==== ORDER MANAGEMENT AGENT INTENTS =====
    "create_order": {
        "description": "Create a new order (minimal: item_name and quantity; customer_phone defaults from the messaging user id; pricing may be added later)",
        "slots": [
            "customer_name",
            "customer_phone",
            "item_name",
            "quantity",
            "unit_price",
            "subtotal_amount",
            "discount_amount",
            "tax_amount",
            "shipping_amount",
            "currency_code",
            "customer_email",
            "customer_location",
            "order_type",
            "order_source",
        ],
        "required_slots": ["item_name", "quantity"],
        "category": "order_management"
    },
    "update_order": {
        "description": "Update details of an existing order",
        "slots": ["order_id", "customer_name", "item_name", "quantity"],
        "required_slots": ["order_id"],
        "category": "order_management"
    },
    "send_order_invoice": {
        "description": "Generate a Paystack payment link for an order and send the invoice to the customer's chat",
        "slots": ["order_id", "order_number", "customer_email"],
        "required_slots": [],
        "category": "order_management"
    },
    #==== FINANCIAL TIPS AGENT INTENTS =====
    "financial_tips": {
        "description": "Provide general financial advice and tips",
        "slots": ["category", "time_period", "goal"],
        "required_slots": [],
        "category": "financial_tips"
    },
    "budgeting_advice": {
        "description": "Provide budgeting recommendations",
        "slots": ["income_level", "expense_category", "savings_goal"],
        "required_slots": [],
        "category": "financial_tips"
    },
    "savings_tips": {
        "description": "Offer savings strategies and advice",
        "slots": ["savings_goal", "timeframe", "current_savings"],
        "required_slots": [],
        "category": "financial_tips"
    },
    "investment_advice": {
        "description": "Provide basic investment guidance",
        "slots": ["risk_tolerance", "investment_amount", "time_horizon"],
        "required_slots": [],
        "category": "financial_tips"
    },
    
    # ===== TRANSACTIONAL INTENTS =====
    "send_money": {
        "description": "Send money to another person",
        "slots": ["recipient", "amount", "network", "reference", "customer_name"],
        "required_slots": ["recipient", "amount", "reference"],
        "category": "transactional"
    },
    "pay_bill": {
        "description": "Pay utility bills",
        "slots": ["bill_type", "account_number", "amount", "provider"],
        "required_slots": ["bill_type", "account_number", "amount"],
        "category": "transactional"
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
    
    # ===== SYSTEM INTENTS =====
    "intent_not_clear": {
        "description": "Intent could not be determined from the message",
        "slots": [],
        "required_slots": [],
        "category": "system"
    },
    "request_intervention": {
        "description": "User requests a human agent / support intervention",
        "slots": ["reason"],
        "required_slots": [],
        "category": "system"
    },
    "end_intervention": {
        "description": "User indicates they no longer need a human agent and want the bot to continue",
        "slots": [],
        "required_slots": [],
        "category": "system"
    }
    
}

# Shown in chat prompts: never answer as the software vendor/platform.
VENDOR_EXCLUSION_RULES = """
- You represent the customer's organization (see Organization context), not the software platform or its vendor.
- Never describe Autobus, Greenbrain, or any underlying platform unless that exact information appears in Retrieved memory for this tenant.
- For questions about "your company", "we", or "our business", use only Organization context and Retrieved memory. If neither contains the answer, say you do not have that information yet and suggest adding business documents to the knowledge base.
"""

# Enhanced System Prompts by Category
SYSTEM_PROMPTS = {
    "conversational": """
    You are an expert AI-powered assistant for the customer's organization.
    Through natural interactions (text, voice, and image messages), help with business operations
    and financial management for that organization only.

    Capabilities you may help with when relevant: conversational assistance, email, media generation,
    products, orders, customer profiles, business information, and customer support — always scoped
    to the tenant organization.

    CRUCIAL RESPONSE GUIDELINES:
    - Be warm, engaging, and natural.
    - Keep responses short and to the point.
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
    
    "financial_tips": """
    You are a knowledgeable financial advisor assistant for the customer's organization in Ghana and Africa.
    Provide practical, culturally relevant financial advice. Focus on:
    - Savings techniques that work in local contexts
    - Investment opportunities in the region
    - Debt management specific to African economies

    The following comprises of the user's spending data.
    If there is no user financial data available, return with a message indicating user data not acquired yet.

    Current User context: {context}
    Financial topic: {category}

    Notes for accuracy:
        - Keep response very short and concise.
        - Tailor advice to local economic conditions.
        - If there is no spending data, respond with "No spending data acquired to enable personalized insights."
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

    "transactional": """
    You are a financial assistant for the customer's organization in Ghana. You help with:
    - Sending money via Mobile Money (MoMo)
    - Buying airtime and data bundles
    - Paying bills (utilities, TV subscriptions, etc.)
    - Expense tracking and budgeting
    - Loan applications
    - Financial advice and insights
    
    Always be conversational, helpful, and clear. Ask for missing information politely.
    If unsure, ask clarifying questions.
    
    Current User Context: {context}
    Missing slots: {missing_slots}
    """,

    "customers": """
    You are a business assistant for the customer's organization in Ghana. You can help users with managing customers.
    Focus on:
    - Adding new customers
    - Viewing saved customers
    - Deleting customers

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
        "greeting_named": "Welcome back, {name}! What task are we handling today?",
        "greeting_anonymous": "Welcome back! What task are we handling today?",
        "customer_greeting_named": "Hi! Welcome to {business}. How can we help you today?",
        "customer_greeting_anonymous": "Hi! How can we help you today?",
        "customer_goodbye": "Thanks for reaching out! Feel free to message us anytime.",
        "normal_conversation": "{response}",
        "business_conversation": "{response}",
        "small_talk": "{response}",
        "goodbye": "Goodbye! 👋 Feel free to reach out if you need help with your business operations."
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
    
    "product_management": {
        "add_product": "✅ Product '{product_name}' has been added to inventory. Price: {price}, Quantity: {quantity}",
        "update_product": "✅ Product '{product_name}' (ID: {product_id}) has been updated successfully.",
        "delete_product": "✅ Product (ID: {product_id}) has been removed from inventory.",
        "view_products": "Here are all products in your inventory: {products_list}",
        "view_product": "📦 Product Details:\n{product_details}",
        "missing_slots": "I'd be happy to help with product management. Please provide: {missing_slots}",
        "error": "I apologize, but I encountered an error. Please try again."
    },
    
    "order_management": {
        "create_order": "✅ Order created successfully!\nCustomer: {customer_name}\nItem: {item_name}\nQuantity: {quantity}\nOrder ID: {order_id}",
        "update_order": "✅ Order (ID: {order_id}) has been updated successfully.",
        "send_order_invoice": "✅ Invoice with Paystack payment link sent for order {order_number}.",
        "missing_slots": "I'd be happy to help with order management. Please provide: {missing_slots}",
        "error": "I apologize, but I encountered an error processing your order. Please try again."
    },
    
    "financial_tips": {
        "financial_tips": "💡 {response}",
        "budgeting_advice": "📊 Budgeting Tip: {response}",
        "savings_tips": "💰 Savings Advice: {response}",
        "investment_advice": "📈 Investment Insight: {response}",
        "debt_management": "🎯 Debt Strategy: {response}"
    },
    
    "expense_report": {
        "success": "Your expense report has been generated successfully! Here are the details: {details}",
        "error": "I apologize, but I couldn't generate the expense report. Please try again."
    },

    "transactional": {
        "missing_slots": "I'd be happy to help you {intent}. I just need a few more details: {missing_slots}",
        "confirm_action": "Great! I'm ready to {intent}. Please confirm with your PIN to proceed.",
        "error": "I apologize, but I'm having trouble processing your request. Please try again.",
        "success": "Your {intent} has been processed successfully! {details}"
    },
    "customers": {
        "add_customer": "The customer {customer_name} has been added successfully.",
        "view_customers": "Here are your saved customers: {customers_list}",
        "delete_customer": "The customer {customer_name} has been removed successfully.",
        "update_customer": "The customer {customer_name} has been updated successfully."
    },
    "system": {
        "intent_not_clear": "I'm not quite sure what you're asking. Could you please rephrase or provide more details? I can help you with: sending money, buying airtime, paying bills, tracking expenses, managing customers, or getting financial tips."
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
    "product_management": ["add_product", "update_product", "delete_product", "view_products", "view_product"],
    "order_management": ["create_order", "update_order", "send_order_invoice"],
    "financial_tips": ["financial_tips", "budgeting_advice", "savings_tips", "investment_advice", "debt_management"],
    "transactional": ["send_money", "buy_airtime", "pay_bill", "check_balance", "get_loan", "track_expenses", "set_budget"],
    "expense_report": ["expense_report", "generate_expense_report", "monthly_expense_summary",  "annual_expense_report", "daily_expense_report","transaction_info"],
    "customers": ["add_customer", "view_customers", "delete_customer", "update_customer"],
    "user_management": ["update_user_details", "update_username", "update_phone_number", "view_user_profile"],
    "system": ["intent_not_clear", "request_intervention", "end_intervention"]
}
