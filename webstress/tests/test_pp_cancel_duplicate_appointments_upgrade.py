"""Solvability + near-miss proof for the upgraded pp_cancel_duplicate_appointments.

The upgraded task is HARD: three appointments share the exact same datetime,
each booked at a different time. The agent must KEEP only the earliest-booked
one and CANCEL the other two, each with an exact cancellation_reason. The
keep/cancel partition is a seed-computed discriminator (the agent cannot infer
it from id order — booked_at ordering is shuffled per seed).

The CORRECT solution is driven through the REAL backend cancel endpoint via
starlette TestClient, using the seed `targets` as the intended answer; this
confirms the answer is achievable past every backend guard. A WRONG/near-miss
trajectory (cancelling the wrong member of the cluster) must fail.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_cancel_duplicate_appointments"
REASON = "Duplicate booking - keeping earliest"
PREFIX = "/api/env/patient_portal"


def _create_session(client: TestClient, seed: int = 42):
    # Create the session directly on the manager the app uses (so the
    # TestClient routes operate on the same session) and read back the
    # seed-resolved targets — the intended canonical answer.
    sid, targets, _ = app.state.session_manager.create_session(
        env_id="patient_portal", task_id=TASK_ID, seed=seed,
    )
    return sid, dict(targets)


def _cancel(client: TestClient, sid: str, apt_id: str, reason: str = REASON):
    return client.post(
        f"{PREFIX}/appointments/{apt_id}/cancel",
        json={"session_id": sid, "reason": reason},
    )


def _state(sid: str):
    return app.state.session_manager.get_state(sid)


def test_correct_trajectory_passes_via_real_backend():
    """Cancelling exactly the non-earliest cluster members (with the exact
    reason) through the real cancel endpoint evaluates to a pass."""
    with TestClient(app) as client:
        sid, targets = _create_session(client, seed=42)

        cancel_ids = targets["conflict_cancel_apt_ids"]
        keep_id = targets["conflict_keep_apt_id"]
        assert isinstance(cancel_ids, list) and len(cancel_ids) == 2
        assert keep_id not in cancel_ids

        # Drive the CORRECT solution through the real backend route.
        for apt_id in cancel_ids:
            r = _cancel(client, sid, apt_id)
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "cancelled"

        state = _state(sid)
        # Backend persisted the cancellations + the exact reason.
        for apt_id in cancel_ids:
            apt = state.get_appointment(apt_id)
            assert apt.status == "cancelled"
            assert apt.cancellation_reason == REASON
        # The kept (earliest-booked) appointment is untouched.
        assert state.get_appointment(keep_id).status == "scheduled"

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"result: {result}"


def test_wrong_member_cancelled_fails():
    """Cancelling the WRONG cluster member (the earliest-booked one that must
    be KEPT) and leaving a to-cancel member scheduled must fail — both the
    bijection (wrong slot) and the critical preserve invariant on the kept
    appointment are violated."""
    with TestClient(app) as client:
        sid, targets = _create_session(client, seed=42)

        cancel_ids = list(targets["conflict_cancel_apt_ids"])
        keep_id = targets["conflict_keep_apt_id"]

        # Cancel only ONE correct member, plus the appointment that should be
        # kept — a plausible near-miss for an agent that mis-orders booked_at.
        _cancel(client, sid, cancel_ids[0])
        _cancel(client, sid, keep_id)

        state = _state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result: {result}"


def test_wrong_reason_fails():
    """Cancelling exactly the right two appointments but with the WRONG
    cancellation reason must fail the changes.cancellation_reason predicate."""
    with TestClient(app) as client:
        sid, targets = _create_session(client, seed=42)
        cancel_ids = list(targets["conflict_cancel_apt_ids"])

        for apt_id in cancel_ids:
            _cancel(client, sid, apt_id, reason="cancelled it")

        state = _state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result: {result}"


def test_under_cancel_fails():
    """Cancelling only ONE of the two required members (under-acting) must
    fail the exact-cardinality bijection ('no fewer')."""
    with TestClient(app) as client:
        sid, targets = _create_session(client, seed=7)
        cancel_ids = list(targets["conflict_cancel_apt_ids"])

        _cancel(client, sid, cancel_ids[0])

        state = _state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result: {result}"
