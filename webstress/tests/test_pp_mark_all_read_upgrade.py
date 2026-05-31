"""Solvability proof for the upgraded pp_mark_all_read task.

The task was re-tiered easy -> medium: the agent must mark read ONLY the unread
clinical + scheduling messages (the "actionable care-team" subset) while leaving
every unread billing message unread. This defeats the one-click
POST /messages/mark-all-read shortcut.

Correct solution is driven through the REAL backend mutation endpoint
``POST /api/env/patient_portal/messages/{id}/read`` via TestClient, sharing the
app's session manager so the mutation lands on the same state we evaluate. Wrong
trajectories (the mark-all-read shortcut, and a partial-only-clinical pass) are
asserted to fail.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

_PREFIX = "/api/env/patient_portal"


def _shared_manager() -> SessionManager:
    """Return the SessionManager the running ``app`` actually uses.

    The PP routes resolve the manager from ``app.state.session_manager``; reusing
    it here means a session created via the manager is visible to TestClient
    calls and vice-versa, so endpoint mutations land on the state we evaluate.
    """
    sm = getattr(app.state, "session_manager", None)
    if sm is None:
        sm = SessionManager()
        app.state.session_manager = sm
    return sm


def _create_session() -> tuple[str, dict, object]:
    sm = _shared_manager()
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id="pp_mark_all_read", seed=42
    )
    return sid, dict(targets), sm.get_state(sid)


def test_correct_trajectory_via_real_endpoint_passes():
    """Marking exactly the clinical+scheduling unread messages via the real
    read endpoint evaluates to a pass."""
    sid, targets, state = _create_session()
    actionable = list(targets["clinical_unread_msg_ids"]) + list(
        targets["scheduling_unread_msg_ids"]
    )
    assert actionable, "seed produced no actionable unread messages"
    assert targets["billing_unread_msg_ids"], "seed produced no protected billing unread"

    client = TestClient(app)
    for msg_id in actionable:
        resp = client.post(
            f"{_PREFIX}/messages/{msg_id}/read", json={"session_id": sid}
        )
        assert resp.status_code == 200, resp.text

    task = get_task("pp_mark_all_read")
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than the legacy 2-check eval: bijection + 7 invariants + 2 constraints.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_mark_all_read_shortcut_fails():
    """The one-click mark-all-read shortcut over-marks billing -> CRITICAL
    constraint violation -> fail."""
    sid, targets, state = _create_session()

    client = TestClient(app)
    resp = client.post(f"{_PREFIX}/messages/mark-all-read", json={"session_id": sid})
    assert resp.status_code == 200, resp.text

    task = get_task("pp_mark_all_read")
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"shortcut should fail: {result}"


def test_partial_only_clinical_fails():
    """Reading only the clinical subset (skipping scheduling) leaves the
    bijection unsaturated and the cardinality constraint violated -> fail."""
    sid, targets, state = _create_session()
    only_clinical = list(targets["clinical_unread_msg_ids"])

    client = TestClient(app)
    for msg_id in only_clinical:
        resp = client.post(
            f"{_PREFIX}/messages/{msg_id}/read", json={"session_id": sid}
        )
        assert resp.status_code == 200, resp.text

    task = get_task("pp_mark_all_read")
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"partial should fail: {result}"
