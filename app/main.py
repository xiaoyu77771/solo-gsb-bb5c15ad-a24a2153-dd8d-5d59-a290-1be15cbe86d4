"""Batch fund-flow reconciliation API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from .models import GroupOut, ReconcileRequest, ReconcileResponse
from .solver import (
    LockedGroup,
    LockedGroupError,
    Reconciler,
    Txn,
    resolve_locked_groups,
)

app = FastAPI(
    title="Batch Reconciliation API",
    version="1.0.0",
    summary="Match fund flows from two sides into optimal reconciliation groups.",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/reconcile", response_model=ReconcileResponse)
def reconcile(payload: ReconcileRequest) -> ReconcileResponse:
    ids = [t.id for t in payload.left] + [t.id for t in payload.right]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="duplicate transaction id in batch")

    left = [Txn(id=t.id, ts=t.epoch_seconds(), amount=t.amount) for t in payload.left]
    right = [Txn(id=t.id, ts=t.epoch_seconds(), amount=t.amount) for t in payload.right]

    reconciler = Reconciler(left, right)

    locked_cands = []
    if payload.locked_groups:
        locked_in = [
            LockedGroup(left_ids=tuple(g.left_ids), right_ids=tuple(g.right_ids))
            for g in payload.locked_groups
        ]
        try:
            locked_cands = resolve_locked_groups(reconciler, locked_in)
        except LockedGroupError as exc:
            raise RequestValidationError(
                [
                    {
                        "type": "locked_group.invalid",
                        "loc": ["body", "locked_groups", exc.index],
                        "msg": str(exc),
                        "input": payload.locked_groups[exc.index].model_dump(),
                    }
                ]
            )

    groups = reconciler.solve(locked_cands)
    groups.sort(key=lambda c: c.encoding)

    matched_left = {i for c in groups for i in c.left_ids}
    matched_right = {i for c in groups for i in c.right_ids}

    return ReconcileResponse(
        groups=[
            GroupOut(left_ids=list(c.left_ids), right_ids=list(c.right_ids), amount=c.amount)
            for c in groups
        ],
        total_amount=sum(c.weight for c in groups),
        unmatched_left_ids=sorted(t.id for t in payload.left if t.id not in matched_left),
        unmatched_right_ids=sorted(t.id for t in payload.right if t.id not in matched_right),
    )
