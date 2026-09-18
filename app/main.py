"""Batch fund-flow reconciliation API."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .models import GroupOut, ReconcileRequest, ReconcileResponse
from .solver import Reconciler, Txn

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

    groups = Reconciler(left, right).solve()
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
