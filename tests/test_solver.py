"""Solver tests, including a differential check against brute force."""

from __future__ import annotations

import random

import pytest

from app.solver import Reconciler, Txn

BASE_TS = 1_758_000_000.0  # fixed epoch for reproducibility


def make_txn(id: str, amount: int, ts: float = BASE_TS) -> Txn:
    return Txn(id=id, ts=ts, amount=amount)


def brute_force(left: list[Txn], right: list[Txn]):
    """Enumerate every packing of legal candidates; return (max weight,
    lexicographically smallest sorted encoding list among max-weight packings)."""
    rec = Reconciler(left, right)
    cands = rec.candidates
    by_txn: list[list[int]] = [[] for _ in rec.txns]
    for ci, c in enumerate(cands):
        m = c.mask
        while m:
            b = m & (-m)
            by_txn[b.bit_length() - 1].append(ci)
            m &= m - 1

    best_weight = 0
    best_enc: list | None = None

    def search(mask: int, weight: int, chosen: list[int]) -> None:
        nonlocal best_weight, best_enc
        if mask == 0:
            return
        b = mask & (-mask)
        t = b.bit_length() - 1
        # t unmatched
        search(mask & ~b, weight, chosen)
        # t inside one candidate
        for ci in by_txn[t]:
            cm = cands[ci].mask
            if cm & mask == cm:
                chosen.append(ci)
                new_weight = weight + cands[ci].weight
                enc = sorted(cands[j].encoding for j in chosen)
                if new_weight > best_weight or (
                    new_weight == best_weight and (best_enc is None or enc < best_enc)
                ):
                    best_weight = new_weight
                    best_enc = enc
                search(mask ^ cm, new_weight, chosen)
                chosen.pop()

    full = (1 << len(rec.txns)) - 1
    search(full, 0, [])
    return best_weight, (best_enc or [])


def solve(left: list[Txn], right: list[Txn]):
    groups = Reconciler(left, right).solve()
    weight = sum(g.weight for g in groups)
    enc = sorted(g.encoding for g in groups)
    return weight, enc


def test_no_candidates():
    assert solve([make_txn("L1", 100)], [make_txn("R1", 101)]) == (0, [])
    assert solve([], []) == (0, [])


def test_simple_match():
    weight, enc = solve([make_txn("L1", 100)], [make_txn("R1", 100)])
    assert weight == 100
    assert enc == [(("L1",), ("R1",))]


@pytest.mark.parametrize("seed", range(250))
def test_against_brute_force(seed: int):
    rng = random.Random(seed)
    n_left = rng.randint(0, 5)
    n_right = rng.randint(0, 5)
    left = [
        Txn(
            id=f"L{i}",
            ts=BASE_TS + rng.randint(0, 4 * 86400),
            amount=rng.choice([-300, -200, -100, -50, 50, 100, 200, 300]),
        )
        for i in range(n_left)
    ]
    right = [
        Txn(
            id=f"R{i}",
            ts=BASE_TS + rng.randint(0, 4 * 86400),
            amount=rng.choice([-300, -200, -100, -50, 50, 100, 200, 300]),
        )
        for i in range(n_right)
    ]
    assert solve(left, right) == brute_force(left, right)


def test_worst_case_full_batch_is_fast():
    # 12 vs 12, all identical amounts and timestamps: every candidate is legal.
    left = [make_txn(f"L{i}", 100) for i in range(12)]
    right = [make_txn(f"R{i}", 100) for i in range(12)]
    weight, enc = solve(left, right)
    assert weight == 1200
    assert len(enc) == 12
    # lexicographically smallest perfect matching pairs L{i} with R{i}
    # (note: encodings sort as strings, so "L10" < "L2")
    assert enc == sorted(((f"L{i}",), (f"R{i}",)) for i in range(12))


def test_large_random_batch_completes():
    rng = random.Random(7)
    left = [
        Txn(f"L{i}", BASE_TS + rng.randint(0, 3 * 86400), rng.randint(-5000, 5000) or 1)
        for i in range(12)
    ]
    right = [
        Txn(f"R{i}", BASE_TS + rng.randint(0, 3 * 86400), rng.randint(-5000, 5000) or 1)
        for i in range(12)
    ]
    weight, enc = solve(left, right)
    # sanity: every transaction appears at most once
    used = [id_ for group in enc for side in group for id_ in side]
    assert len(used) == len(set(used))
    assert weight >= 0
