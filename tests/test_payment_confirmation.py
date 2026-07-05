import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.nlu.service.payment_confirmation import (
    conversation_mentions_payment_confirmation,
    is_affirmative_response,
    resolve_payment_slots,
    should_handle_payment_confirmation,
)


def test_affirmative_responses():
    assert is_affirmative_response("yes")
    assert is_affirmative_response("OK!")
    assert is_affirmative_response("go ahead")


def test_resolve_payment_slots_from_collected_state():
    slots = resolve_payment_slots(
        collected_slots={"amount": "2", "recipient_name": "Anna"},
        pending_payment_dto={},
        conversation_history=[],
    )
    assert slots == {"amount": "2", "recipient_name": "Anna"}


def test_resolve_payment_slots_from_history():
    slots = resolve_payment_slots(
        collected_slots={},
        pending_payment_dto={},
        conversation_history=[
            {"role": "assistant", "content": "To complete the payment, reply yes."},
            {"role": "user", "content": "send 2 cedis to Anna 0207926310"},
        ],
    )
    assert slots == {
        "amount": "2",
        "recipient_phone": "0207926310",
        "recipient_name": "Anna",
    }


def test_should_handle_after_payment_prompt():
    assert conversation_mentions_payment_confirmation(
        "To complete the payment, reply yes to confirm."
    )
    assert should_handle_payment_confirmation(
        user_message="yes",
        current_intent="normal_conversation",
        waiting_for_payment_confirmation=False,
        collected_slots={},
        pending_payment_dto={},
        conversation_history=[
            {"role": "user", "content": "send 2 cedis to Anna 0207926310"},
            {
                "role": "assistant",
                "content": "To complete the payment, reply yes to confirm.",
            },
        ],
    )
