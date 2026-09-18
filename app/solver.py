"""Reconciliation engine.

Builds every legal reconciliation group (candidate) from the two sides, then
selects a mutually exclusive set of candidates maximising the total absolute
reconciled amount.  Ties are broken by choosing the solution whose sorted list
of group encodings -- ``(sorted left ids, sorted right ids)`` -- is
lexicographically smallest.

Group legality rules:
  * shapes: 1-1, or a single record on one side with 2-3 on the other;
  * every amount in the group shares the same sign;
  * the sums of both sides are equal;
  * the span between the earliest and latest timestamps is <= 48 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

MAX_SPAN_SECONDS = 48 * 3600  # 48 hours, inclusive
# (left count, right count) shapes: 1-1 plus one-to-many (2..3) in both directions.
GROUP_SHAPES = ((1, 1), (1, 2), (1, 3), (2, 1), (3, 1))


@dataclass(frozen=True)
class Txn:
    id: str
    ts: float  # UTC epoch seconds
    amount: int  # signed cents, non-zero


@dataclass(frozen=True)
class Candidate:
    """A legal reconciliation group."""

    mask: int  # bitmask over the concatenated transaction list (left first)
    weight: int  # absolute reconciled amount
    left_ids: tuple[str, ...]  # ascending
    right_ids: tuple[str, ...]  # ascending
    amount: int  # signed reconciled amount (sum of either side)

    @property
    def encoding(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (self.left_ids, self.right_ids)


class Reconciler:
    def __init__(self, left: list[Txn], right: list[Txn]) -> None:
        self.left = left
        self.right = right
        self.txns = left + right
        self.n_left = len(left)
        self.amounts = [t.amount for t in self.txns]
        self.candidates: list[Candidate] = self._generate_candidates()
        # candidate indices touching each transaction, heavier first
        self.cand_by_txn: list[list[int]] = [[] for _ in self.txns]
        for ci, cand in enumerate(self.candidates):
            m = cand.mask
            while m:
                b = m & (-m)
                self.cand_by_txn[b.bit_length() - 1].append(ci)
                m &= m - 1
        for lst in self.cand_by_txn:
            lst.sort(key=lambda ci: -self.candidates[ci].weight)
        self._memo: dict[int, int] = {0: 0}

    # ------------------------------------------------------------------ #
    # candidate generation
    # ------------------------------------------------------------------ #
    def _generate_candidates(self) -> list[Candidate]:
        cands: list[Candidate] = []
        left, right, n_left = self.left, self.right, self.n_left
        for size_l, size_r in GROUP_SHAPES:
            if size_l > len(left) or size_r > len(right):
                continue
            for lcomb in combinations(range(len(left)), size_l):
                sum_l = sum(left[i].amount for i in lcomb)
                for rcomb in combinations(range(len(right)), size_r):
                    sum_r = sum(right[j].amount for j in rcomb)
                    if sum_l != sum_r:
                        continue
                    amounts = [left[i].amount for i in lcomb] + [right[j].amount for j in rcomb]
                    if not (all(a > 0 for a in amounts) or all(a < 0 for a in amounts)):
                        continue
                    times = [left[i].ts for i in lcomb] + [right[j].ts for j in rcomb]
                    if max(times) - min(times) > MAX_SPAN_SECONDS:
                        continue
                    mask = 0
                    for i in lcomb:
                        mask |= 1 << i
                    for j in rcomb:
                        mask |= 1 << (n_left + j)
                    cands.append(
                        Candidate(
                            mask=mask,
                            weight=abs(sum_l),
                            left_ids=tuple(sorted(left[i].id for i in lcomb)),
                            right_ids=tuple(sorted(right[j].id for j in rcomb)),
                            amount=sum_l,
                        )
                    )
        return cands

    # ------------------------------------------------------------------ #
    # maximum total weight over a transaction subset (bitmask)
    # ------------------------------------------------------------------ #
    def _max_value(self, mask: int) -> int:
        memo = self._memo
        cached = memo.get(mask)
        if cached is not None:
            return cached

        # Upper bound: every group consumes |amount| from each side, so the
        # achievable total is capped by either side's remaining absolute sum.
        sum_l = sum_r = 0
        m = mask
        n_left, amounts = self.n_left, self.amounts
        while m:
            b = m & (-m)
            i = b.bit_length() - 1
            if i < n_left:
                sum_l += abs(amounts[i])
            else:
                sum_r += abs(amounts[i])
            m &= m - 1
        ub = min(sum_l, sum_r)
        if ub == 0:
            memo[mask] = 0
            return 0

        best = self._greedy_lower_bound(mask)
        if best < ub:
            t = self._choose_txn(mask)
            cands = self.candidates
            # branch: t belongs to one of the candidates
            for ci in self.cand_by_txn[t]:
                cm = cands[ci].mask
                if cm & mask == cm:
                    v = cands[ci].weight + self._max_value(mask ^ cm)
                    if v > best:
                        best = v
                        if best == ub:
                            break
            # branch: t stays unmatched
            if best < ub:
                v = self._max_value(mask & ~(1 << t))
                if v > best:
                    best = v
        memo[mask] = best
        return best

    def _greedy_lower_bound(self, mask: int) -> int:
        value = 0
        m = mask
        cands = self.candidates
        while m:
            b = m & (-m)
            t = b.bit_length() - 1
            placed = False
            for ci in self.cand_by_txn[t]:
                cm = cands[ci].mask
                if cm & m == cm:
                    value += cands[ci].weight
                    m ^= cm
                    placed = True
                    break
            if not placed:
                m &= m - 1
        return value

    def _choose_txn(self, mask: int) -> int:
        """Pick the transaction with the fewest currently usable candidates."""
        best_t = -1
        best_count: int | None = None
        m = mask
        cands = self.candidates
        while m:
            b = m & (-m)
            t = b.bit_length() - 1
            m &= m - 1
            cnt = 0
            for ci in self.cand_by_txn[t]:
                cm = cands[ci].mask
                if cm & mask == cm:
                    cnt += 1
                    if best_count is not None and cnt >= best_count:
                        break
            if best_count is None or cnt < best_count:
                best_count = cnt
                best_t = t
                if cnt == 0:
                    break
        return best_t

    # ------------------------------------------------------------------ #
    # optimal solution with lexicographically smallest sorted encodings
    # ------------------------------------------------------------------ #
    def solve(self) -> list[Candidate]:
        if not self.candidates:
            return []
        full = (1 << len(self.txns)) - 1
        remaining_value = self._max_value(full)
        # A candidate can open an optimal residual solution iff the rest still
        # achieves the remaining optimum; taking the smallest such encoding at
        # every step yields the lexicographically smallest sorted encoding list.
        ordered = sorted(range(len(self.candidates)), key=lambda ci: self.candidates[ci].encoding)
        solution: list[Candidate] = []
        mask = full
        while remaining_value > 0:
            for ci in ordered:
                cand = self.candidates[ci]
                if cand.weight > remaining_value or cand.mask & mask != cand.mask:
                    continue
                if self._max_value(mask ^ cand.mask) == remaining_value - cand.weight:
                    solution.append(cand)
                    mask ^= cand.mask
                    remaining_value -= cand.weight
                    break
            else:  # pragma: no cover - defensive, cannot happen if _max_value is exact
                raise RuntimeError("no feasible candidate for the remaining optimum")
        return solution
