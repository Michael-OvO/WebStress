"""Solvability proof for the upgraded lms_drop_course task.

The upgraded task no longer names the course to drop. Instead the agent must
re-derive which single enrolled course can no longer reach a B (weighted score
>= 80%) given the recorded grades and remaining assignments, then drop ONLY
that course's enrollment while leaving every other enrollment frozen.

The correct solution is driven through the REAL backend mutation endpoint
(POST /api/env/lms/courses/{course_id}/drop) via starlette TestClient so the
proof also confirms the action is reachable past every gate (enrollment exists,
status == 'enrolled', now <= drop_deadline).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _create_session(seed: int):
    """Create a session on the app's own SessionManager and return (client, sid, targets, state)."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="lms", task_id="lms_drop_course", seed=seed)
    state = sm.get_state(sid)
    client = TestClient(app)
    return client, sid, dict(targets), state


def _enrollment_status(state, enrollment_id):
    return next(e.status for e in state.enrollments if e.id == enrollment_id)


def test_correct_drop_of_unrecoverable_course_passes():
    """Dropping exactly the unrecoverable course's enrollment via the real endpoint passes."""
    client, sid, targets, state = _create_session(seed=42)

    # The seed precomputes the single impossible-to-B course + its enrollment.
    impossible_course_id = targets["impossible_course_id"]
    impossible_enrollment_id = targets["impossible_enrollment_id"]
    assert impossible_course_id, "seed must expose exactly one impossible course id"
    assert impossible_enrollment_id, "seed must expose exactly one impossible enrollment id"

    # Sanity: before the drop the enrollment is 'enrolled'.
    assert _enrollment_status(state, impossible_enrollment_id) == "enrolled"

    # Drive the CORRECT solution through the real mutation endpoint (past all gates).
    resp = client.post(
        f"/api/env/lms/courses/{impossible_course_id}/drop",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json().get("dropped") is True

    # Confirm only the impossible enrollment flipped to 'dropped'.
    assert _enrollment_status(state, impossible_enrollment_id) == "dropped"
    dropped = [e for e in state.enrollments if e.status == "dropped"]
    assert len(dropped) == 1

    task = get_task("lms_drop_course")
    result = evaluate(
        task=task,
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # canonical_diff is richer than the original single-update easy task.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_dropping_wrong_course_fails():
    """Dropping an achievable course instead of the unrecoverable one fails."""
    client, sid, targets, state = _create_session(seed=42)

    impossible_course_id = targets["impossible_course_id"]
    achievable = [c for c in targets["achievable_course_ids"].split(",") if c]
    wrong_course_id = achievable[0]
    assert wrong_course_id and wrong_course_id != impossible_course_id

    resp = client.post(
        f"/api/env/lms/courses/{wrong_course_id}/drop",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    task = get_task("lms_drop_course")
    result = evaluate(
        task=task,
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    # Wrong target: the required update did not match AND the protected-enrollment
    # invariant was violated (a sibling enrollment changed).
    assert result.get("success") is False, f"result: {result}"


def test_over_dropping_both_courses_fails():
    """Dropping the correct course PLUS a sibling (extra side-effect) fails."""
    client, sid, targets, state = _create_session(seed=42)

    impossible_course_id = targets["impossible_course_id"]
    achievable = [c for c in targets["achievable_course_ids"].split(",") if c]
    extra_course_id = achievable[0]

    r1 = client.post(
        f"/api/env/lms/courses/{impossible_course_id}/drop",
        json={"session_id": sid},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        f"/api/env/lms/courses/{extra_course_id}/drop",
        json={"session_id": sid},
    )
    assert r2.status_code == 200, r2.text

    task = get_task("lms_drop_course")
    result = evaluate(
        task=task,
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    # The primary update is satisfied, but the extra drop violates the
    # critical sibling-enrollment invariant and the "exactly one dropped"
    # constraint, so the task must fail.
    assert result.get("success") is False, f"result: {result}"


def test_collateral_message_fails():
    """Dropping the right course but also sending a message fails (critical constraint)."""
    client, sid, targets, state = _create_session(seed=42)

    impossible_course_id = targets["impossible_course_id"]
    r1 = client.post(
        f"/api/env/lms/courses/{impossible_course_id}/drop",
        json={"session_id": sid},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        "/api/env/lms/messages/send",
        json={"session_id": sid, "to": "Advisor", "subject": "FYI", "body": "Dropped a course."},
    )
    assert r2.status_code == 200, r2.text

    task = get_task("lms_drop_course")
    result = evaluate(
        task=task,
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
