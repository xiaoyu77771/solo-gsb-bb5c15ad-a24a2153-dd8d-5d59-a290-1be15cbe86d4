"""API-level tests for the reconciliation endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

T0 = "2026-09-17T00:00:00Z"


def txn(id: str, amount: int, time: str = T0) -> dict:
    return {"id": id, "time": time, "amount": amount}


def post(left, right):
    resp = client.post("/reconcile", json={"left": left, "right": right})
    assert resp.status_code == 200, resp.text
    return resp.json()


# --------------------------------------------------------------------- #
# basic matching
# --------------------------------------------------------------------- #
def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_empty_batch():
    assert post([], []) == {
        "groups": [],
        "total_amount": 0,
        "unmatched_left_ids": [],
        "unmatched_right_ids": [],
    }


def test_one_to_one():
    body = post([txn("L1", 100)], [txn("R1", 100)])
    assert body["groups"] == [{"left_ids": ["L1"], "right_ids": ["R1"], "amount": 100}]
    assert body["total_amount"] == 100
    assert body["unmatched_left_ids"] == []
    assert body["unmatched_right_ids"] == []


def test_one_to_many_shapes():
    # 1 left vs 2 right
    body = post([txn("L1", 300)], [txn("R1", 100), txn("R2", 200)])
    assert body["groups"] == [{"left_ids": ["L1"], "right_ids": ["R1", "R2"], "amount": 300}]
    # 3 left vs 1 right
    body = post([txn("L1", 100), txn("L2", 200), txn("L3", 50)], [txn("R1", 350)])
    assert body["groups"] == [{"left_ids": ["L1", "L2", "L3"], "right_ids": ["R1"], "amount": 350}]
    # 2 left vs 1 right and 1 left vs 3 right
    body = post([txn("L1", 60), txn("L2", 40)], [txn("R1", 100)])
    assert body["groups"] == [{"left_ids": ["L1", "L2"], "right_ids": ["R1"], "amount": 100}]
    body = post([txn("L1", 600)], [txn("R1", 100), txn("R2", 200), txn("R3", 300)])
    assert body["groups"] == [{"left_ids": ["L1"], "right_ids": ["R1", "R2", "R3"], "amount": 600}]


def test_negative_amounts_group():
    body = post([txn("L1", -150)], [txn("R1", -100), txn("R2", -50)])
    assert body["groups"] == [{"left_ids": ["L1"], "right_ids": ["R1", "R2"], "amount": -150}]
    assert body["total_amount"] == 150


def test_mixed_signs_form_separate_groups():
    # same-sign groups only: L1+R1 (positive) and L2+R2 (negative)
    body = post([txn("L1", 100), txn("L2", -100)], [txn("R1", 100), txn("R2", -100)])
    assert body["groups"] == [
        {"left_ids": ["L1"], "right_ids": ["R1"], "amount": 100},
        {"left_ids": ["L2"], "right_ids": ["R2"], "amount": -100},
    ]
    assert body["total_amount"] == 200


def test_cross_sign_group_not_allowed():
    # L1=100, R1=-100 can never form a group (sums differ and signs differ)
    body = post([txn("L1", 100)], [txn("R1", -100)])
    assert body["groups"] == []


def test_sum_mismatch_no_match():
    body = post([txn("L1", 100)], [txn("R1", 101)])
    assert body["groups"] == []
    assert body["total_amount"] == 0
    assert body["unmatched_left_ids"] == ["L1"]
    assert body["unmatched_right_ids"] == ["R1"]


# --------------------------------------------------------------------- #
# 48-hour window
# --------------------------------------------------------------------- #
def test_48h_window_inclusive():
    body = post([txn("L1", 100, T0)], [txn("R1", 100, "2026-09-19T00:00:00Z")])
    assert len(body["groups"]) == 1


def test_beyond_48h_no_match():
    body = post([txn("L1", 100, T0)], [txn("R1", 100, "2026-09-19T00:00:01Z")])
    assert body["groups"] == []


def test_window_spans_whole_group():
    # two right records 48h apart can pair with one left only if the whole
    # group fits inside 48 hours
    body = post(
        [txn("L1", 300, "2026-09-18T00:00:00Z")],
        [txn("R1", 100, T0), txn("R2", 200, "2026-09-19T00:00:00Z")],
    )
    assert len(body["groups"]) == 1
    # pushing R2 one second past the 48h span breaks the group
    body = post(
        [txn("L1", 300, "2026-09-18T00:00:00Z")],
        [txn("R1", 100, T0), txn("R2", 200, "2026-09-19T00:00:01Z")],
    )
    assert body["groups"] == []


def test_timezone_offsets_normalised():
    body = post(
        [txn("L1", 100, "2026-09-17T08:00:00+08:00")],  # == 2026-09-17T00:00:00Z
        [txn("R1", 100, T0)],
    )
    assert len(body["groups"]) == 1


def test_naive_datetime_treated_as_utc():
    body = post([txn("L1", 100, "2026-09-17T00:00:00")], [txn("R1", 100, T0)])
    assert len(body["groups"]) == 1


# --------------------------------------------------------------------- #
# optimality and tie-breaking
# --------------------------------------------------------------------- #
def test_prefers_larger_total_amount():
    # (A,Y)=100 competes with (A+B,X)=150 -> the latter wins
    body = post([txn("A", 100), txn("B", 50)], [txn("X", 150), txn("Y", 100)])
    assert body["groups"] == [{"left_ids": ["A", "B"], "right_ids": ["X"], "amount": 150}]
    assert body["total_amount"] == 150
    assert body["unmatched_right_ids"] == ["Y"]


def test_each_transaction_used_at_most_once():
    # X could pair with A or B, not both
    body = post([txn("A", 100), txn("B", 100)], [txn("X", 100)])
    assert body["total_amount"] == 100
    assert len(body["groups"]) == 1


def test_tie_break_picks_lexicographically_smallest():
    # two optimal solutions: {(A,X),(B,Y)} vs {(A,Y),(B,X)} -> first wins
    body = post([txn("B", 100), txn("A", 100)], [txn("Y", 100), txn("X", 100)])
    assert body["groups"] == [
        {"left_ids": ["A"], "right_ids": ["X"], "amount": 100},
        {"left_ids": ["B"], "right_ids": ["Y"], "amount": 100},
    ]


def test_tie_break_across_group_shapes():
    # (A,[X,Y]) and (A,[Z]) both reconcile 300 -> ([A],[X,Y]) < ([A],[Z])
    body = post(
        [txn("A", 300)],
        [txn("Z", 300), txn("X", 100), txn("Y", 200)],
    )
    assert body["groups"] == [{"left_ids": ["A"], "right_ids": ["X", "Y"], "amount": 300}]


def test_groups_sorted_and_unmatched_listed():
    body = post(
        [txn("L2", 100), txn("L1", 200), txn("L3", 999)],
        [txn("R2", 100), txn("R1", 200), txn("R9", 888)],
    )
    assert [g["left_ids"] for g in body["groups"]] == [["L1"], ["L2"]]
    assert body["unmatched_left_ids"] == ["L3"]
    assert body["unmatched_right_ids"] == ["R9"]


# --------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------- #
def test_duplicate_ids_return_400():
    dup = client.post("/reconcile", json={"left": [txn("X", 1), txn("X", 2)], "right": []})
    assert dup.status_code == 400
    cross = client.post("/reconcile", json={"left": [txn("X", 1)], "right": [txn("X", 1)]})
    assert cross.status_code == 400


def test_zero_amount_rejected():
    resp = client.post("/reconcile", json={"left": [txn("L1", 0)], "right": []})
    assert resp.status_code == 422


def test_non_integer_amount_rejected():
    for bad in (1.5, True, "100"):
        resp = client.post("/reconcile", json={"left": [txn("L1", bad)], "right": []})
        assert resp.status_code == 422, bad


def test_more_than_12_per_side_rejected():
    resp = client.post(
        "/reconcile",
        json={"left": [txn(f"L{i}", 1) for i in range(13)], "right": []},
    )
    assert resp.status_code == 422


def test_bad_time_rejected():
    resp = client.post("/reconcile", json={"left": [txn("L1", 1, "not-a-time")], "right": []})
    assert resp.status_code == 422


def test_empty_id_rejected():
    resp = client.post("/reconcile", json={"left": [txn("", 1)], "right": []})
    assert resp.status_code == 422


def test_full_batch_of_12_per_side():
    left = [txn(f"L{i}", 100 * (1 if i % 2 == 0 else -1)) for i in range(12)]
    right = [txn(f"R{i}", 100 * (1 if i % 2 == 0 else -1)) for i in range(12)]
    body = post(left, right)
    assert body["total_amount"] == 1200
    assert len(body["groups"]) == 12
