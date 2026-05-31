"""Solvability proof for the upgraded lms_submit_assignment task.

The task was re-tiered from easy -> medium. It now requires the agent to:
  * identify EVERY past-due, not-yet-submitted assignment that is still inside
    its course's late-submission window (days_late <= max_late_days),
  * submit each one (a bijection that must SATURATE),
  * upload a per-course file named "catchup_<COURSE_CODE>.pdf",
  * NOT submit any unrecoverable (past-window) assignment (critical constraint),
  * keep every sibling collection frozen.

The correct solution is driven through the REAL backend submit endpoint
(POST /api/env/lms/assignments/{id}/submit) via TestClient, so the proof also
confirms the action is achievable past every server gate. The session is created
on app.state.session_manager so the TestClient endpoints and the evaluator
operate on the same state object.
"""

from __future__ import annotations

from datetime import datetime, timezone

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_submit_assignment"


def _split(csv: str) -> list[str]:
    return [x for x in (csv or "").split(",") if x]


def _new_session(seed: int = 42):
    """Create a session on the shared app SessionManager and return (sid, targets, state)."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=seed)
    state = sm.get_state(sid)
    return sid, dict(targets), state


def _file_for(state, assignment_id: str) -> str:
    a = state.get_assignment(assignment_id)
    code = state.get_course(a.course_id).course_code
    return f"catchup_{code}.pdf"


def test_correct_trajectory_via_real_submit_endpoint_passes() -> None:
    """Submitting every recoverable-missing assignment through the real endpoint passes."""
    client = TestClient(app)
    sid, targets, state = _new_session()
    recoverable = _split(targets["recoverable_missing_assignment_ids"])
    assert recoverable, "seed must produce at least one recoverable missing assignment"

    try:
        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": _file_for(state, aid)},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()["assignment"]
            assert body["submission_status"] in ("submitted", "late")

        state = app.state.session_manager.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
        # Richer than a single-check eval: bijection + sibling guards.
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2
    finally:
        app.state.session_manager.destroy(sid)


def test_submitting_unrecoverable_assignment_fails() -> None:
    """Submitting a past-window (unrecoverable) assignment trips the critical constraint."""
    client = TestClient(app)
    sid, targets, state = _new_session()
    recoverable = _split(targets["recoverable_missing_assignment_ids"])
    unrecoverable = _split(targets["unrecoverable_assignment_ids"])
    assert unrecoverable, "seed must produce at least one unrecoverable assignment"

    try:
        # Do the correct recoverable submissions...
        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": _file_for(state, aid)},
            )
            assert resp.status_code == 200, resp.text
        # ...but ALSO submit an unrecoverable one (the most-tempting wrong move).
        bad = unrecoverable[0]
        resp = client.post(
            f"/api/env/lms/assignments/{bad}/submit",
            json={"session_id": sid, "file_name": _file_for(state, bad)},
        )
        assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"unrecoverable submit should fail: {result}"
    finally:
        app.state.session_manager.destroy(sid)


def test_partial_submission_does_not_saturate_bijection_fails() -> None:
    """Submitting only some recoverable assignments fails to saturate the bijection."""
    client = TestClient(app)
    sid, targets, state = _new_session()
    recoverable = _split(targets["recoverable_missing_assignment_ids"])
    if len(recoverable) < 2:
        # With only one recoverable target the bijection cannot be partially
        # satisfied; assert via a wrong-file-name near-miss instead.
        aid = recoverable[0]
        resp = client.post(
            f"/api/env/lms/assignments/{aid}/submit",
            json={"session_id": sid, "file_name": "wrong_name.pdf"},
        )
        assert resp.status_code == 200, resp.text
        state = app.state.session_manager.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        try:
            assert result.get("success") is False, f"wrong file name should fail: {result}"
        finally:
            app.state.session_manager.destroy(sid)
        return

    try:
        # Submit all but the last recoverable assignment.
        for aid in recoverable[:-1]:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": _file_for(state, aid)},
            )
            assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"partial submission should fail: {result}"
    finally:
        app.state.session_manager.destroy(sid)


def test_wrong_file_name_fails() -> None:
    """Correct assignment set but wrong file-naming convention fails."""
    client = TestClient(app)
    sid, targets, state = _new_session()
    recoverable = _split(targets["recoverable_missing_assignment_ids"])

    try:
        for aid in recoverable:
            resp = client.post(
                f"/api/env/lms/assignments/{aid}/submit",
                json={"session_id": sid, "file_name": "submission.pdf"},
            )
            assert resp.status_code == 200, resp.text

        state = app.state.session_manager.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"wrong file name should fail: {result}"
    finally:
        app.state.session_manager.destroy(sid)
