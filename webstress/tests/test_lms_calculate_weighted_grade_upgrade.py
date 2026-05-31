"""Solvability proof for the hardened lms_calculate_weighted_grade task.

Drives the CORRECT solution through the REAL LMS backend endpoints (Starlette
TestClient over the full ASGI app) and confirms the canonical_diff evaluator
passes; then drives wrong / near-miss trajectories and confirms they fail.

The hardened task is a 2-branch oneof gated on `has_discrepancy`:
  * Branch 1 (a course has a >1pt discrepancy): resubmit the single most
    recently graded assignment (`most_recent_graded_id`) with grade_dispute.pdf.
  * Branch 2 (no discrepancy): mark the latest announcement in the target
    course as read.

seed=42 exercises Branch 1; seed=1 exercises Branch 2.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.security import CONTROLLER_SECRET_HEADER
from webstress.backend.state import SessionManager
from webstress.runner import ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_calculate_weighted_grade"


def _client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {CONTROLLER_SECRET_HEADER: app.state.controller_secret}


def _create(client: TestClient, seed: int) -> tuple[str, dict]:
    resp = client.post(
        f"/api/env/lms/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["session_id"], body["resolved_targets"]


def _evaluate_endpoint(client: TestClient, session_id: str) -> dict:
    resp = client.post(
        "/api/env/lms/evaluate",
        json={"session_id": session_id, "task_id": TASK_ID, "trajectory": []},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Branch 1 (has_discrepancy == 'true') — resubmit most_recent_graded_id
# ---------------------------------------------------------------------------

def test_branch1_correct_resubmit_passes_via_real_endpoint():
    """seed=42 has a discrepancy; resubmitting most_recent_graded_id passes.

    Method: REAL backend endpoint POST /assignments/{id}/submit (the submit
    route accepts a 'graded' assignment with remaining attempts and bumps
    attempt_count, setting submission_status to submitted/late and file_name).
    """
    client = _client()
    sid, targets = _create(client, seed=42)
    assert targets["has_discrepancy"] == "true"

    mr_id = targets["most_recent_graded_id"]
    resp = client.post(
        f"/api/env/lms/assignments/{mr_id}/submit",
        json={"session_id": sid, "file_name": "grade_dispute.pdf"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["assignment"]["file_name"] == "grade_dispute.pdf"

    result = _evaluate_endpoint(client, sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    # richer than legacy 2-check eval
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_branch1_correct_passes_via_evaluate_function():
    """Same Branch-1 outcome, asserted through the unified evaluate() entry point."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    assert dict(targets)["has_discrepancy"] == "true"

    mr_id = dict(targets)["most_recent_graded_id"]
    a = state.get_assignment(mr_id)
    # Mirror the submit endpoint's effect on the assignment record.
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    a.attempt_count += 1
    a.file_name = "grade_dispute.pdf"
    a.submitted_at = now
    a.submission_status = "late" if now > a.due_at else "submitted"

    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99


# ---------------------------------------------------------------------------
# Branch 2 (has_discrepancy == 'false') — mark latest announcement read
# ---------------------------------------------------------------------------

def test_branch2_correct_mark_read_passes_via_real_endpoint():
    """seed=1 has no discrepancy; marking the latest announcement read passes."""
    client = _client()
    sid, targets = _create(client, seed=1)
    assert targets["has_discrepancy"] == "false"

    ann_id = targets["latest_announcement_id"]
    resp = client.post(
        f"/api/env/lms/announcements/{ann_id}/read",
        json={"session_id": sid},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["announcement"]["is_read"] is True

    result = _evaluate_endpoint(client, sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99


# ---------------------------------------------------------------------------
# Wrong / near-miss trajectories — must FAIL
# ---------------------------------------------------------------------------

def test_wrong_branch_on_discrepancy_fails():
    """seed=42 HAS a discrepancy, but agent takes the no-discrepancy action
    (marks announcement read instead of resubmitting). Neither branch matches:
    branch 1's positive update is unsatisfied, branch 2's where-clause requires
    has_discrepancy == 'false'. Must fail."""
    client = _client()
    sid, targets = _create(client, seed=42)
    assert targets["has_discrepancy"] == "true"

    ann_id = targets["latest_announcement_id"]
    resp = client.post(
        f"/api/env/lms/announcements/{ann_id}/read",
        json={"session_id": sid},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate_endpoint(client, sid)
    assert result.get("success") is False, f"result: {result}"


def test_wrong_assignment_resubmit_fails():
    """seed=42: resubmit a DIFFERENT graded assignment (not most_recent_graded_id).
    Branch 1's positive update is unsatisfied and the collateral invariant on
    other assignments fires. Must fail."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    t = dict(targets)
    assert t["has_discrepancy"] == "true"
    mr_id = t["most_recent_graded_id"]

    # Find a graded, resubmittable assignment that is NOT the most_recent one.
    wrong = next(
        a for a in state.assignments
        if a.id != mr_id and a.submission_status == "graded"
        and a.attempt_count < a.max_attempts
    )
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    wrong.attempt_count += 1
    wrong.file_name = "grade_dispute.pdf"
    wrong.submitted_at = now
    wrong.submission_status = "late" if now > wrong.due_at else "submitted"

    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"result: {result}"


def test_grade_tampering_fails_even_with_correct_resubmit():
    """seed=42: agent resubmits the correct assignment BUT also mutates a grade
    record (critical-severity invariant on state.grades). Must fail."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    t = dict(targets)
    mr_id = t["most_recent_graded_id"]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    a = state.get_assignment(mr_id)
    a.attempt_count += 1
    a.file_name = "grade_dispute.pdf"
    a.submitted_at = now
    a.submission_status = "late" if now > a.due_at else "submitted"

    # Tamper with a grade record (forbidden, critical invariant).
    from decimal import Decimal
    assert state.grades, "expected seeded grades"
    state.grades[0].score = (Decimal(str(state.grades[0].score)) + Decimal("5"))

    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"result: {result}"
