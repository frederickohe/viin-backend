"""Credit resource types and per-plan allocation defaults."""

from enum import Enum
from typing import Dict


class CreditType(str, Enum):
    LLM = "llm"
    IMAGE_GEN = "image_gen"
    VIDEO_GEN = "video_gen"
    EMAIL = "email"
    SMS = "sms"
    STORAGE_MB = "storage_mb"
    SERVER = "server"


CREDIT_TYPE_LABELS: Dict[str, str] = {
    CreditType.LLM.value: "LLM Chats",
    CreditType.IMAGE_GEN.value: "Image Gen",
    CreditType.VIDEO_GEN.value: "Video Gen",
    CreditType.EMAIL.value: "Email",
    CreditType.SMS.value: "SMS",
    CreditType.STORAGE_MB.value: "Storage (MB)",
    CreditType.SERVER.value: "Server Requests",
}

# Monthly allocations keyed by plan name (case-insensitive lookup in service).
PLAN_CREDIT_DEFAULTS: Dict[str, Dict[str, float]] = {
    "free": {
        CreditType.LLM.value: 25,
        CreditType.IMAGE_GEN.value: 3,
        CreditType.VIDEO_GEN.value: 1,
        CreditType.EMAIL.value: 5,
        CreditType.SMS.value: 5,
        CreditType.STORAGE_MB.value: 250,
        CreditType.SERVER.value: 1000,
    },
    "starter": {
        CreditType.LLM.value: 100,
        CreditType.IMAGE_GEN.value: 10,
        CreditType.VIDEO_GEN.value: 2,
        CreditType.EMAIL.value: 25,
        CreditType.SMS.value: 25,
        CreditType.STORAGE_MB.value: 2560,  # 2.5 GB
        CreditType.SERVER.value: 10000,
    },
    "standard": {
        CreditType.LLM.value: 1000,
        CreditType.IMAGE_GEN.value: 50,
        CreditType.VIDEO_GEN.value: 10,
        CreditType.EMAIL.value: 100,
        CreditType.SMS.value: 100,
        CreditType.STORAGE_MB.value: 25600,  # 25 GB
        CreditType.SERVER.value: 10000,
    },
    "business": {
        CreditType.LLM.value: 3000,
        CreditType.IMAGE_GEN.value: 110,
        CreditType.VIDEO_GEN.value: 20,
        CreditType.EMAIL.value: 200,
        CreditType.SMS.value: 200,
        CreditType.STORAGE_MB.value: 102400,  # 100 GB
        CreditType.SERVER.value: 1000000,
    },
}

ALL_CREDIT_TYPES = [ct.value for ct in CreditType]
