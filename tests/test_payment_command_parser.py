import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.nlu.service.payment_command_parser import try_parse_payment_command


def test_send_cedis_to_name_and_phone():
    slots = try_parse_payment_command("send 2 cedis to Anna 0207926310")
    assert slots == {
        "amount": "2",
        "recipient_phone": "0207926310",
        "recipient_name": "Anna",
    }


def test_pay_cedis_to_name_only():
    slots = try_parse_payment_command("pay 50 cedis to John")
    assert slots == {"amount": "50", "recipient_name": "John"}


def test_make_a_payment_of_amount():
    slots = try_parse_payment_command("make a payment of 25 GHS")
    assert slots == {"amount": "25"}


def test_send_with_description():
    slots = try_parse_payment_command("send 100 cedis to Ama for school fees")
    assert slots == {
        "amount": "100",
        "recipient_name": "Ama",
        "description": "school fees",
    }


def test_ignores_non_payment_messages():
    assert try_parse_payment_command("send me the report") is None
    assert try_parse_payment_command("hello Anna") is None


if __name__ == "__main__":
    test_send_cedis_to_name_and_phone()
    test_pay_cedis_to_name_only()
    test_make_a_payment_of_amount()
    test_send_with_description()
    test_ignores_non_payment_messages()
    print("ok")
