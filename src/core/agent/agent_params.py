"""Central registry of agent required parameters.

Each agent maps to a list of required parameter keys that must be present
in the user's agent config stored in `User.agents`.
"""

AGENT_REQUIRED_PARAMS = {
    "config_agent": [],
    "email_agent": ["sender_email"],
    "chatbot_agent": [],
    "image_generation_agent": [],
    "video_generation_agent": [],
    "web_search_agent": [],
}
