"""Actual page metadata stays aligned through priority and overflow packing."""
from agent.delivery.budget import utf16_len
from agent.delivery.formatter import format_split, format_split_tracked
from agent.delivery.message import Item, Message


def test_packing_metadata_partitions_actual_items_not_matching_text():
    items = (Item(headline="یکسان", detail="شرح " * 16, priority=2),
             Item(headline="یکسان", detail="شرح " * 16, priority=0),
             Item(headline="یکسان", detail="شرح " * 16, priority=1))
    message = Message(header="یکسان", items=items, footer="یکسان")
    for budget in range(1, 260, 7):
        forms = []
        pages, dropped = format_split_tracked(message, max_units=budget, max_messages=2,
                                             page_forms=forms)
        assert pages == format_split(message, max_units=budget, max_messages=2)
        assert len(pages) == len(forms)
        assert all(utf16_len(page) <= budget for page in pages)
        packed = [i for page in forms for i in page]
        assert len(packed) == len(set(packed))
        assert set(packed).isdisjoint(dropped)
        assert set(packed) | dropped == set(range(len(items)))
        assert {form for page in forms for form in page.values()} <= {"full", "headline_only"}


def test_metadata_distinguishes_full_detail_from_headline_only():
    message = Message(header="H", items=(Item(headline="title", detail="x" * 30, priority=0),))
    forms = []
    format_split_tracked(message, max_units=20, page_forms=forms)
    assert forms == [{0: "headline_only"}]
    forms = []
    format_split_tracked(message, max_units=100, page_forms=forms)
    assert forms == [{0: "full"}]
