import hashlib
import secrets
from typing import Optional

class SecurityManager:
    def __init__(self):
        # In production, use a proper database
        self.user_pins = {}  # user_id -> hashed_pin
    
    def set_user_pin(self, user_id: str, pin: str) -> bool:
        """Set user PIN (during onboarding)"""
        if len(pin) == 5 and pin.isdigit():
            self.user_pins[user_id] = self._hash_pin(pin)
            return True
        return False
    
    def verify_pin(self, user_id: str, pin: str) -> bool:
        """Verify user PIN"""
        if user_id in self.user_pins:
            return self.user_pins[user_id] == self._hash_pin(pin)
        return False
    
    def _hash_pin(self, pin: str) -> str:
        """Hash PIN for storage"""
        return hashlib.sha256(pin.encode()).hexdigest()
    
    def is_pin_required(self, intent: str) -> bool:
        """Check if PIN is required for an intent"""
        secure_intents = ["make_payment"]
        return intent in secure_intents