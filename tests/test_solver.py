"""Solver tests, including a differential check against brute force."""

from __future__ import annotations

import random

import pytest

from app.solver import LockedGroupError, Reconciler, Txn, validate_locked_groups

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


# --------------------------------------------------------------------- #
# locked groups
# --------------------------------------------------------------------- #
def test_solve_keeps_locked_groups():
    # unconstrained optimum would be (A+B, X)=150; locking (A, Y) forces it
    left = [make_txn("A", 100), make_txn("B", 50)]
    right = [make_txn("X", 150), make_txn("Y", 100)]
    rec = Reconciler(left, right)
    locked = [rec.locked_candidate(["A"], ["Y"])]
    groups = rec.solve(locked=locked)
    assert sorted(g.encoding for g in groups) == [(("A",), ("Y",))]


def test_solve_with_locked_covering_everything():
    left = [make_txn("L1", 100)]
    right = [make_txn("R1", 100)]
    rec = Reconciler(left, right)
    locked = [rec.locked_candidate(["L1"], ["R1"])]
    assert [g.encoding for g in rec.solve(locked=locked)] == [(("L1",), ("R1",))]


def test_solve_without_locked_unchanged():
    left = [make_txn("A", 100), make_txn("B", 50)]
    right = [make_txn("X", 150), make_txn("Y", 100)]
    rec = Reconciler(left, right)
    assert rec.solve() == rec.solve(locked=[]) == rec.solve(locked=None)


def test_validate_locked_groups_accepts_all_shapes():
    left = [
        make_txn("L1", 100),
        make_txn("L2", 150),
        make_txn("L3", 200),
        make_txn("L4", 70),
        make_txn("L5", 80),
        make_txn("L6", 10),
        make_txn("L7", 20),
        make_txn("L8", 30),
    ]
    right = [
        make_txn("R1", 100),
        make_txn("R2", 60),
        make_txn("R3", 90),
        make_txn("R4", 50),
        make_txn("R5", 50),
        make_txn("R6", 100),
        make_txn("R7", 150),
        make_txn("R8", 60),
    ]
    validate_locked_groups(
        left,
        right,
        [
            (["L1"], ["R1"]),  # 1-1
            (["L2"], ["R2", "R3"]),  # 1-2
            (["L3"], ["R4", "R5", "R6"]),  # 1-3
            (["L4", "L5"], ["R7"]),  # 2-1
            (["L6", "L7", "L8"], ["R8"]),  # 3-1
        ],
    )


def test_validate_locked_groups_error_messages():
    left = [make_txn("L1", 100), make_txn("L2", 100)]
    right = [make_txn("R1", 100), make_txn("R2", 100)]
    with pytest.raises(LockedGroupError, match=r"locked_groups\[0\].*no right transaction"):
        validate_locked_groups(left, right, [(["L1"], ["R9"])])
    with pytest.raises(LockedGroupError, match=r"locked_groups\[1\].*used more than once"):
        validate_locked_groups(left, right, [(["L1"], ["R1"]), (["L1"], ["R2"])])
    with pytest.raises(LockedGroupError, match=r"locked_groups\[0\].*shape 2-2"):
        validate_locked_groups(left, right, [(["L1", "L2"], ["R1", "R2"])])
    with pytest.raises(LockedGroupError, match=r"locked_groups\[0\].*same sign"):
        validate_locked_groups(
            [make_txn("L1", 100)], [make_txn("R1", 150), make_txn("R2", -50)],
            [(["L1"], ["R1", "R2"])],
        )
    with pytest.raises(LockedGroupError, match=r"locked_groups\[0\].*sums differ"):
        validate_locked_groups(left, right, [(["L1", "L2"], ["R1"])])
    with pytest.raises(LockedGroupError, match=r"locked_groups\[0\].*48 hours"):
        validate_locked_groups(
            [make_txn("L1", 100, ts=BASE_TS)],
            [make_txn("R1", 100, ts=BASE_TS + 48 * 3600 + 1)],
            [(["L1"], ["R1"])],
        )


@pytest.mark.parametrize("seed", range(100))
def test_locked_against_reduced_instance(seed: int):
    """Solving with locked groups must equal solving the remaining instance."""
    rng = random.Random(10_000 + seed)
    n_left = rng.randint(1, 5)
    n_right = rng.randint(1, 5)
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
    rec = Reconciler(left, right)
    # pick up to two disjoint legal candidates as locked groups
    locked = []
    locked_mask = 0
    for cand in rng.sample(rec.candidates, k=min(2, len(rec.candidates))):
        if cand.mask & locked_mask == 0:
            locked.append(cand)
            locked_mask |= cand.mask
    groups = rec.solve(locked=locked)
    # the residual solution must match solving the reduced instance directly
    rem_left = [t for i, t in enumerate(left) if not (locked_mask >> i) & 1]
    rem_right = [t for j, t in enumerate(right) if not (locked_mask >> (n_left + j)) & 1]
    residual = Reconciler(rem_left, rem_right).solve()
    expected = sorted([c.encoding for c in locked] + [c.encoding for c in residual])
    assert sorted(g.encoding for g in groups) == expected
