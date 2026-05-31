"""Solvability proof for the upgraded ``lms_view_late_policy`` task.

The task was re-tiered from easy to medium. The agent must read EACH course's
late policy, re-derive the set of recoverable overdue assignments (past due but
within the course's max_late_days), and submit every one of them with
``late_submit.pdf`` (bijection saturation) — while leaving the unrecoverable
overdue assignments (past max late days) and already-submitted work untouched,
and sending no messages.

The correct trajectory is driven through the REAL backend submit endpoint via
starlette TestClient so the proof also confirms the action is reachable past the
``/assignments/{id}/submit`` gates.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.routes.lms import SessionCreateRequest, create_session
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _split(csv: str) -> list[str]:
    return [x for x in (csv or "").split(",") if x]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _new_session() -> tuple[str, dict]:
    payload = create_session(
        SessionCreateRequest(task_id="lms_view_late_policy", seed=42),
        session_manager=app.state.session_manager,
    )
    sid = payload["session_id"]
    state = app.state.session_manager.get(sid)
    return sid, dict(state.resolved_targets)


def test_seed_fixture_is_non_vacuous() -> None:
    """The recoverable set must be non-empty and the unrecoverable trap present."""
    sid, targets = _new_session()
    try:
        recoverable = _split(targets["recoverable_missing_assignment_ids"])
        unrecoverable = _split(targets["unrecoverable_assignment_ids"])
        assert recoverable, "expected at least one recoverable overdue assignment"
        assert unrecoverable, "expected unrecoverable overdue decoys (the trap)"
        # The two sets must be disjoint or the task is contradictory.
        assert not (set(recoverable) & set(unrecoverable))
        assert targets["allows_late_submit"] == "true"
    finally:
        app.state.session_manager.destroy(sid)


def test_correct_trajectory_passes(client: TestClient) -> None:
    """Submitting every recoverable overdue assignment via the real endpoint passes."""
    sid, targets = _new_session()
    try:
        recoverable = _split(targets["recoverable_missing_assignment_ids"])
        assert recoverable

        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": "late_submit.pdf"},
            )
            assert resp.status_code == 200, resp.text
            # These are overdue, so the backend marks them 'late'.
            assert resp.json()["assignment"]["submission_status"] in ("late", "submitted")

        state = app.state.session_manager.get(sid)
        result = evaluate(
            task=get_task("lms_view_late_policy"),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
        # Richer than a 2-check eval (bijection + invariants + constraints).
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2
    finally:
        app.state.session_manager.destroy(sid)


def test_submitting_unrecoverable_assignment_fails(client: TestClient) -> None:
    """Submitting an unrecoverable (past-max-late-days) assignment is a critical miss."""
    sid, targets = _new_session()
    try:
        recoverable = _split(targets["recoverable_missing_assignment_ids"])
        unrecoverable = _split(targets["unrecoverable_assignment_ids"])
        assert unrecoverable

        # Do the right thing for the recoverable set...
        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": "late_submit.pdf"},
            )
            assert resp.status_code == 200, resp.text

        # ...but ALSO submit a forbidden unrecoverable overdue assignment.
        bad = unrecoverable[0]
        resp = client.post(
            f"/api/env/lms/assignments/{bad}/submit",
            json={"session_id": sid, "file_name": "late_submit.pdf"},
        )
        assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get(sid)
        result = evaluate(
            task=get_task("lms_view_late_policy"),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result should fail: {result}"
    finally:
        app.state.session_manager.destroy(sid)


def test_missing_a_recoverable_assignment_fails(client: TestClient) -> None:
    """If there is more than one recoverable assignment, skipping one fails.

    When only one recoverable assignment exists for seed 42, submitting nothing
    leaves the bijection unsaturated, which must also fail.
    """
    sid, targets = _new_session()
    try:
        recoverable = _split(targets["recoverable_missing_assignment_ids"])
        # Submit all but the last recoverable assignment (or nothing if only one).
        for aid in recoverable[:-1]:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": "late_submit.pdf"},
            )
            assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get(sid)
        result = evaluate(
            task=get_task("lms_view_late_policy"),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result should fail (unsaturated): {result}"
    finally:
        app.state.session_manager.destroy(sid)


def test_sending_message_fails(client: TestClient) -> None:
    """Sending a message violates the high-severity no-message constraint."""
    sid, targets = _new_session()
    try:
        recoverable = _split(targets["recoverable_missing_assignment_ids"])
        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": "late_submit.pdf"},
            )
            assert resp.status_code == 200, resp.text

        resp = client.post(
            "/api/env/lms/messages/send",
            json={
                "session_id": sid,
                "to": "Advisor",
                "subject": "Late work",
                "body": "I have submitted my recoverable overdue work.",
            },
        )
        assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get(sid)
        result = evaluate(
            task=get_task("lms_view_late_policy"),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"result should fail (message sent): {result}"
    finally:
        app.state.session_manager.destroy(sid)
