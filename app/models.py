"""Pydantic schemas for the batch reconciliation API."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator

MAX_TRANSACTIONS_PER_SIDE = 12


class TransactionIn(BaseModel):
    """One fund-flow record.

    - ``id``: batch-unique identifier (non-empty string).
    - ``time``: ISO-8601 timestamp; naive values are treated as UTC.
    - ``amount``: non-zero signed integer amount in cents.
    """

    id: StrictStr = Field(min_length=1, max_length=128)
    time: datetime
    amount: StrictInt

    @field_validator("amount")
    @classmethod
    def amount_must_be_non_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("amount must be a non-zero integer (cents)")
        return v

    def epoch_seconds(self) -> float:
        """Return the timestamp as UTC epoch seconds."""
        dt = self.time
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()


class ReconcileRequest(BaseModel):
    """Batch of fund flows from two sides (at most 12 each)."""

    left: list[TransactionIn] = Field(default_factory=list, max_length=MAX_TRANSACTIONS_PER_SIDE)
    right: list[TransactionIn] = Field(default_factory=list, max_length=MAX_TRANSACTIONS_PER_SIDE)


class GroupOut(BaseModel):
    """One reconciliation group: sorted id lists of both sides."""

    left_ids: list[str]
    right_ids: list[str]
    amount: int  # signed reconciled amount in cents (sum of either side)


class ReconcileResponse(BaseModel):
    groups: list[GroupOut]
    total_amount: int  # sum of absolute reconciled amounts across groups
    unmatched_left_ids: list[str]
    unmatched_right_ids: list[str]
