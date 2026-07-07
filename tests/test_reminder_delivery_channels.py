from types import SimpleNamespace

from core.memory.service.reminder_delivery_service import ReminderDeliveryService


def _reminder(**kwargs):
    defaults = {
        "delivery": {"channels": ["telegram", "sms"], "telegram_chat_id": "7819176159"},
        "owner_user_id": "gfNRYhAKZnZMfVVCBGcB",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _user(**kwargs):
    return SimpleNamespace(
        in_app_notification=False,
        sms_notification=False,
        phone="+233247291736",
        **kwargs,
    )


def test_explicit_reminder_channels_ignore_notification_prefs():
    service = ReminderDeliveryService()
    reminder = _reminder()
    user = _user()

    assert service.effective_channels(reminder, user) == ["telegram", "sms"]


def test_default_channels_still_respect_notification_prefs():
    service = ReminderDeliveryService()
    reminder = _reminder(delivery={}, owner_user_id="user-123")
    user = _user()

    assert service.effective_channels(reminder, user) == []
