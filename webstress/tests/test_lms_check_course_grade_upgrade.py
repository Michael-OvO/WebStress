"""Solvability proof for the upgraded (medium) lms_check_course_grade task.

Drives the CORRECT solution through the REAL LMS backend mutation endpoints
(submit / mark-announcement-read) via Starlette TestClient, then evaluates via
the unified evaluator. Also proves that wrong / near-miss trajectories fail.

Branch 1 (grade below 80%): submit the next unsubmitted homework with
catch_up.pdf in the target course. Seed 42 lands here.
Branch 2 (grade >= 80%): mark EVERY unread announcement in the target course as
read (bijection). Seed 33 lands here.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.security import CONTROLLER_SECRET_HEADER
from webstress.runner import ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_check_course_grade"
# Seed 42 -> grade_below_80 == 'true' (branch 1). Seed 33 -> 'false' (branch 2).
BELOW_SEED = 42
ABOVE_SEED = 33


@pytest.fixture()
def client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {CONTROLLER_SECRET_HEADER: app.state.controller_secret}


def _create(client: TestClient, seed: int) -> dict:
    resp = client.post(
        "/api/env/lms/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "resolved_targets" in data, "controller header should expose resolved_targets"
    return data


def _state(session_id: str):
    return app.state.session_manager.get(session_id)


def _evaluate(session_id: str) -> dict:
    state = _state(session_id)
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(state.resolved_targets),
        trajectory=[],
    )


# ---------------------------------------------------------------------------
# Branch 1: grade below 80% -> submit catch_up.pdf for the unsubmitted homework
# ---------------------------------------------------------------------------

def test_branch1_correct_submission_passes(client: TestClient):
    session = _create(client, BELOW_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    assert targets["grade_below_80"] == "true", "seed precondition: branch 1 is active"

    hw_id = targets["unsubmitted_hw_id"]
    resp = client.post(
        f"/api/env/lms/assignments/{hw_id}/submit",
        json={"session_id": sid, "file_name": "catch_up.pdf"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # canonical_diff coverage is richer than the legacy 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_branch1_wrong_assignment_fails(client: TestClient):
    """Submitting a DIFFERENT assignment trips the critical sibling invariant."""
    session = _create(client, BELOW_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    hw_id = targets["unsubmitted_hw_id"]

    # Find any other submittable assignment in the session and submit it instead.
    state = _state(sid)
    wrong = next(
        a for a in state.assignments
        if a.id != hw_id
        and a.submission_status in ("not_submitted", "resubmit_requested", "graded", "late")
        and a.attempt_count < a.max_attempts
    )
    resp = client.post(
        f"/api/env/lms/assignments/{wrong.id}/submit",
        json={"session_id": sid, "file_name": "catch_up.pdf"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-assignment trajectory should fail: {result}"


def test_branch1_wrong_filename_fails(client: TestClient):
    """Submitting the right assignment with the wrong file fails the positive op."""
    session = _create(client, BELOW_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    hw_id = targets["unsubmitted_hw_id"]

    resp = client.post(
        f"/api/env/lms/assignments/{hw_id}/submit",
        json={"session_id": sid, "file_name": "wrong_file.pdf"},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-filename trajectory should fail: {result}"


def test_branch1_no_action_fails(client: TestClient):
    """Doing nothing leaves the positive obligation unmet."""
    session = _create(client, BELOW_SEED)
    sid = session["session_id"]
    result = _evaluate(sid)
    assert result.get("success") is False, f"no-op trajectory should fail: {result}"


def test_branch1_wrong_branch_marking_announcement_fails(client: TestClient):
    """Taking branch 2's action (mark announcement read) when branch 1 is correct fails."""
    session = _create(client, BELOW_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    unread = [
        a for a in targets["target_course_unread_announcement_ids"].split(",")
        if a
    ]
    assert unread, "expected at least one unread announcement in target course"
    for ann_id in unread:
        resp = client.post(
            f"/api/env/lms/announcements/{ann_id}/read",
            json={"session_id": sid},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-branch trajectory should fail: {result}"


# ---------------------------------------------------------------------------
# Branch 2: grade >= 80% -> mark EVERY unread announcement in target course read
# ---------------------------------------------------------------------------

def test_branch2_correct_mark_all_unread_passes(client: TestClient):
    session = _create(client, ABOVE_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    assert targets["grade_below_80"] == "false", "seed precondition: branch 2 is active"

    unread = [a for a in targets["target_course_unread_announcement_ids"].split(",") if a]
    assert len(unread) >= 2, f"branch 2 should require a multi-element bijection: {unread}"
    for ann_id in unread:
        resp = client.post(
            f"/api/env/lms/announcements/{ann_id}/read",
            json={"session_id": sid},
            headers=_headers(),
        )
        assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"


def test_branch2_partial_mark_fails(client: TestClient):
    """Marking only ONE of the unread announcements fails the bijection saturation."""
    session = _create(client, ABOVE_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    unread = [a for a in targets["target_course_unread_announcement_ids"].split(",") if a]
    assert len(unread) >= 2

    resp = client.post(
        f"/api/env/lms/announcements/{unread[0]}/read",
        json={"session_id": sid},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(sid)
    assert result.get("success") is False, f"partial-mark trajectory should fail: {result}"
