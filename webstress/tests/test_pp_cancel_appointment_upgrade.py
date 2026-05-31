"""Solvability proof for the hardened pp_cancel_appointment task.

The task was re-tiered from easy -> medium. The patient has two duplicate
appointments with the SAME PCP at the SAME datetime; the only thing that
distinguishes them is ``booked_at``. The agent must cancel the EARLIER-booked
duplicate (minimum booked_at) with a cancellation reason that contains the
word "duplicate", while keeping the later-booked appointment and every other
collection untouched.

The correct trajectory is driven through the REAL backend cancel endpoint
(POST /appointments/{id}/cancel) via a Starlette TestClient so the proof also
confirms the action is reachable past the endpoint's gates (scheduled-only).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.runner import controller_headers, ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "pp_cancel_appointment"
SEED = 42


def _client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _new_session() -> tuple[str, dict]:
    """Create a session on the app's shared SessionManager and return (sid, targets)."""
    sm: SessionManager = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="patient_portal", task_id=TASK_ID, seed=SEED)
    return sid, dict(targets)


def _state(sid: str):
    return app.state.session_manager.get_state(sid)


def _cancel(client: TestClient, sid: str, apt_id: str, reason: str):
    return client.post(
        f"/api/env/patient_portal/appointments/{apt_id}/cancel",
        json={"session_id": sid, "reason": reason},
        headers=controller_headers(),
    )


def _earlier_booked_id(targets: dict, state) -> str:
    """Re-derive the earlier-booked duplicate the way the evaluator expr does."""
    by_id = {a.id: a for a in state.appointments}
    return min(
        targets["conflict_apt_ids"],
        key=lambda aid: (by_id[aid].booked_at, aid),
    )


def test_seed_shape_is_a_same_provider_duplicate_pair():
    """The two conflict appointments share provider + datetime and differ only by booked_at."""
    sid, targets = _new_session()
    state = _state(sid)
    ids = targets["conflict_apt_ids"]
    assert len(ids) == 2
    appts = {a.id: a for a in state.appointments if a.id in ids}
    a0, a1 = appts[ids[0]], appts[ids[1]]
    # Same provider (the PCP) and same datetime -> only booked_at disambiguates.
    assert a0.provider_id == a1.provider_id == state.patient.pcp_id
    assert a0.datetime == a1.datetime
    assert a0.booked_at != a1.booked_at
    # The pre-computed target must equal the min-booked-at appointment.
    assert targets["earlier_booked_apt_id"] == _earlier_booked_id(targets, state)
    assert targets["earlier_booked_apt_id"] != targets["later_booked_apt_id"]


def test_correct_trajectory_passes_via_real_endpoint():
    """Cancelling the earlier-booked duplicate with a 'duplicate' reason passes."""
    client = _client()
    sid, targets = _new_session()
    state = _state(sid)
    target_id = _earlier_booked_id(targets, state)
    assert target_id == targets["target_apt_id"]

    resp = _cancel(client, sid, target_id, "Duplicate booking created in error")
    assert resp.status_code == 200, resp.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Confirm the kept duplicate is still scheduled.
    kept = next(a for a in state.appointments if a.id == targets["later_booked_apt_id"])
    assert kept.status == "scheduled"


def test_wrong_duplicate_cancelled_fails():
    """Cancelling the LATER-booked duplicate (the one that must be kept) fails."""
    client = _client()
    sid, targets = _new_session()
    state = _state(sid)
    wrong_id = targets["later_booked_apt_id"]
    assert wrong_id != _earlier_booked_id(targets, state)

    resp = _cancel(client, sid, wrong_id, "Duplicate booking created in error")
    assert resp.status_code == 200, resp.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_missing_duplicate_reason_fails():
    """Cancelling the right appointment but without 'duplicate' in the reason fails."""
    client = _client()
    sid, targets = _new_session()
    state = _state(sid)
    target_id = _earlier_booked_id(targets, state)

    resp = _cancel(client, sid, target_id, "Patient requested cancellation")
    assert resp.status_code == 200, resp.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_extra_side_effect_cancelling_both_fails():
    """Cancelling BOTH duplicates (extra collateral on the kept one) fails."""
    client = _client()
    sid, targets = _new_session()
    state = _state(sid)
    target_id = _earlier_booked_id(targets, state)

    assert _cancel(client, sid, target_id, "Duplicate booking").status_code == 200
    # Stray side-effect: also cancel the appointment we were told to keep.
    assert _cancel(client, sid, targets["later_booked_apt_id"], "oops").status_code == 200

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
