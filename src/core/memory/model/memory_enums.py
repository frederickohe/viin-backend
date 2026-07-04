import enum


class MemoryItemType(str, enum.Enum):
    NOTE = "NOTE"
    LINK = "LINK"
    FILE = "FILE"
    IMAGE = "IMAGE"
    QUOTE = "QUOTE"
    PASSWORD = "PASSWORD"
    MESSAGE = "MESSAGE"


class MemoryVisibility(str, enum.Enum):
    PRIVATE = "PRIVATE"
    SHARED_1TO1 = "SHARED_1TO1"


class ReminderStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    SENT = "SENT"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class DeliveryStatus(str, enum.Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    FAILED = "FAILED"
