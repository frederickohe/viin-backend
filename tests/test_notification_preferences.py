from types import SimpleNamespace

from core.user.notification_preferences import (
    allows_in_app_notifications,
    allows_sms_notifications,
    channel_allowed,
    filter_channels_by_user_prefs,
)


def _user(**kwargs):
    return SimpleNamespace(**kwargs)


def test_in_app_allowed_by_default():
    user = _user(in_app_notification=None)
    assert allows_in_app_notifications(user) is True


def test_in_app_blocked_when_disabled():
    user = _user(in_app_notification=False)
    assert allows_in_app_notifications(user) is False


def test_sms_requires_explicit_opt_in():
    user = _user(sms_notification=None)
    assert allows_sms_notifications(user) is False

    user = _user(sms_notification=True)
    assert allows_sms_notifications(user) is True


def test_filter_channels_respects_user_prefs():
    user = _user(in_app_notification=False, sms_notification=True)
    assert filter_channels_by_user_prefs(["chat", "sms", "whatsapp"], user) == ["sms"]

    user = _user(in_app_notification=True, sms_notification=False)
    assert filter_channels_by_user_prefs(["chat", "sms", "telegram"], user) == ["chat", "telegram"]


def test_channel_allowed_without_user_record():
    assert channel_allowed("telegram", None) is True
    assert channel_allowed("sms", None) is False
