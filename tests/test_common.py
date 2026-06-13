# tests/test_common.py
from od_cpd.tools._common import direction_of, signed_metric, mm_envelope


def test_direction_of():
    assert direction_of(45) == "later"
    assert direction_of(-3) == "earlier"
    assert direction_of(0) == "unchanged"
    assert direction_of(None) is None


def test_signed_metric():
    assert signed_metric(45) == {"value": 45, "direction": "later"}
    assert signed_metric(-2, kind="budget") == {"value": -2, "direction": "decreased"}
    assert signed_metric(None) == {"value": None, "direction": None}


def test_mm_envelope_count_one_light_caveat():
    env = mm_envelope(anchor_type="schedule", anchor_id="101",
                      linked=[{"fms_id": "ABC", "managing_agency": "DDC"}])
    assert env["anchor"] == {"type": "schedule", "id": "101"}
    assert env["linked_budgets"] == [{"fms_id": "ABC", "managing_agency": "DDC"}]
    assert "1:1" in env["caveat"]


def test_mm_envelope_fanout_lists_all():
    env = mm_envelope(anchor_type="budget", anchor_id="ABC",
                      linked=[{"pid": "1"}, {"pid": "2"}, {"pid": "3"}])
    assert env["linked_schedules"] == [{"pid": "1"}, {"pid": "2"}, {"pid": "3"}]
    assert "3" in env["caveat"]
