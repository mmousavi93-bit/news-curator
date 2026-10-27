"""Guard numeric values, including signs and complete date tokens."""
import pytest
from agent.memory.event_models import Event
from agent.pipeline.synthesis_support import grounded_line


@pytest.mark.parametrize("line", [
    "قیمت نفت به -۷۰ دلار رسید و نشست تهران برگزار شد.",
    "قیمت نفت به −۷۰ دلار رسید و نشست تهران برگزار شد.",
    "قیمت نفت به ۷۰ دلار رسید و نشست در ۱۰/۲۷ برگزار شد.",
    "قیمت نفت به دلار۷۰۰ رسید و نشست تهران برگزار شد.",
    "قیمت نفت به ۷۰e۱۰ دلار رسید و نشست تهران برگزار شد.",
])
def test_new_sign_or_rearranged_date_is_not_supported_by_same_digits(line):
    events = [Event(event_key="oil", summary="قیمت نفت به ۷۰ دلار رسید."),
              Event(event_key="talks", summary="نشست تهران در تاریخ ۲۷/۱۰ برگزار شد.")]
    result = grounded_line({"synthesis": line, "evidence": [
        {"event_id": e.event_key, "quote": e.summary} for e in events]}, events)
    assert result == ("", [])
