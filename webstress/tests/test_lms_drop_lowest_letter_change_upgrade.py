"""Solvability proof for the upgraded (expert) lms_drop_lowest_letter_change task.

Drives the CORRECT solution for BOTH oneof branches through the REAL LMS backend
mutation endpoints (submit / mark-announcement-read) via Starlette TestClient,
then evaluates via the unified evaluator. Also proves that wrong / near-miss
trajectories fail.

The branch discriminator is the GROUNDED flag ``drop_changes_letter_grounded``,
recomputed by the LMSState engine over the FINAL persisted grades using the same
weighted_score_for_course math the agent reads on the Grades page (late-penalty
factor applied, weights re-normalized over graded categories) and the coarse
A>=90 / B>=80 / C>=70 / D>=60 / F<60 scale the instruction names.

Branch 1 (drop RAISES the coarse letter): submit ``letter_grade_report.pdf`` for
``lowest_dropped_hw_id`` -- the homework the engine actually drops (lowest
score-to-points RATIO, NOT necessarily the lowest raw points). Seed 15 lands here.
Branch 2 (drop does NOT change the letter): mark the target course's latest
announcement read. Seed 0 lands here.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.security import CONTROLLER_SECRET_HEADER
from webstress.runner import ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_drop_lowest_letter_change"
# Verified by probing seeds 0-59: grounded discriminator distribution is stable.
# Seed 15 -> drop_changes_letter_grounded == 'true'  (branch 1, submit).
# Seed 0  -> drop_changes_letter_grounded == 'false' (branch 2, mark announcement).
BRANCH1_SEED = 15  # drop raises the letter grade
BRANCH2_SEED = 0   # drop does not change the letter grade


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


def _submit(client: TestClient, sid: str, assignment_id: str, file_name: str) -> None:
    resp = client.post(
        f"/api/env/lms/assignments/{assignment_id}/submit",
        json={"session_id": sid, "file_name": file_name},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text


def _read_announcement(client: TestClient, sid: str, announcement_id: str) -> None:
    resp = client.post(
        f"/api/env/lms/announcements/{announcement_id}/read",
        json={"session_id": sid},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Seed preconditions (fail loudly if the builder drifts and the chosen seeds
# stop landing in their branch -- keeps the solvability proof honest).
# ---------------------------------------------------------------------------

def test_seed_preconditions(client: TestClient):
    b1 = _create(client, BRANCH1_SEED)["resolved_targets"]
    assert b1["drop_changes_letter_grounded"] == "true", b1
    assert b1["lowest_dropped_hw_id"], "branch 1 must have a dropped homework target"

    b2 = _create(client, BRANCH2_SEED)["resolved_targets"]
    assert b2["drop_changes_letter_grounded"] == "false", b2
    assert b2["latest_announcement_id"], "branch 2 must have a latest announcement target"


# ---------------------------------------------------------------------------
# Branch 1: drop raises the letter grade -> submit the dropped homework
# ---------------------------------------------------------------------------

def test_branch1_correct_submission_passes(client: TestClient):
    session = _create(client, BRANCH1_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    assert targets["drop_changes_letter_grounded"] == "true"

    _submit(client, sid, targets["lowest_dropped_hw_id"], "letter_grade_report.pdf")

    result = _evaluate(sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # canonical_diff coverage is richer than the legacy 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_branch1_wrong_homework_raw_lowest_fails(client: TestClient):
    """Submitting the lowest RAW-points homework when it differs from the actually
    dropped (lowest-ratio) homework fails the positive op and trips the critical
    sibling-assignment invariant."""
    session = _create(client, BRANCH1_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    dropped = targets["lowest_dropped_hw_id"]
    raw_lowest = targets["lowest_hw_id"]
    assert raw_lowest and raw_lowest != dropped, (
        "this seed is chosen because the raw-lowest homework differs from the "
        "dropped (lowest-ratio) homework -- a naive agent would pick the wrong one"
    )

    _submit(client, sid, raw_lowest, "letter_grade_report.pdf")

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-homework trajectory should fail: {result}"


def test_branch1_wrong_filename_fails(client: TestClient):
    session = _create(client, BRANCH1_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]

    _submit(client, sid, targets["lowest_dropped_hw_id"], "wrong_file.pdf")

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-filename trajectory should fail: {result}"


def test_branch1_wrong_branch_marking_announcement_fails(client: TestClient):
    """Taking branch 2's action (mark announcement read) when branch 1 is correct
    fails: the positive submit op is unmet AND the read trips a critical invariant."""
    session = _create(client, BRANCH1_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]

    _read_announcement(client, sid, targets["latest_announcement_id"])

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-branch trajectory should fail: {result}"


def test_branch1_no_action_fails(client: TestClient):
    session = _create(client, BRANCH1_SEED)
    sid = session["session_id"]
    result = _evaluate(sid)
    assert result.get("success") is False, f"no-op trajectory should fail: {result}"


# ---------------------------------------------------------------------------
# Branch 2: drop does not change the letter grade -> mark latest announcement read
# ---------------------------------------------------------------------------

def test_branch2_correct_mark_read_passes(client: TestClient):
    session = _create(client, BRANCH2_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]
    assert targets["drop_changes_letter_grounded"] == "false"

    # Precondition: the target latest announcement is in the target course and unread.
    state = _state(sid)
    ann = next(a for a in state.announcements if a.id == targets["latest_announcement_id"])
    assert ann.course_id == targets["target_course_id"], (
        "latest_announcement_id must be scoped to the target course"
    )
    assert ann.is_read is False

    _read_announcement(client, sid, targets["latest_announcement_id"])

    result = _evaluate(sid)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"


def test_branch2_wrong_announcement_fails(client: TestClient):
    """Marking a DIFFERENT (non-latest) announcement read trips the high-severity
    non-target-announcement invariant and leaves the positive op unmet."""
    session = _create(client, BRANCH2_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]

    state = _state(sid)
    other = next(
        a for a in state.announcements
        if a.id != targets["latest_announcement_id"] and not a.is_read
    )
    _read_announcement(client, sid, other.id)

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-announcement trajectory should fail: {result}"


def test_branch2_wrong_branch_submitting_fails(client: TestClient):
    """Taking branch 1's action (submit a homework) when branch 2 is correct fails:
    the read op is unmet AND the submit trips the critical assignment invariant."""
    session = _create(client, BRANCH2_SEED)
    sid = session["session_id"]
    targets = session["resolved_targets"]

    state = _state(sid)
    submittable = next(
        a for a in state.assignments
        if a.course_id == targets["target_course_id"]
        and a.type == "homework"
        and a.submission_status in ("not_submitted", "resubmit_requested", "graded", "late")
        and a.attempt_count < a.max_attempts
    )
    _submit(client, sid, submittable.id, "letter_grade_report.pdf")

    result = _evaluate(sid)
    assert result.get("success") is False, f"wrong-branch trajectory should fail: {result}"


def test_branch2_no_action_fails(client: TestClient):
    session = _create(client, BRANCH2_SEED)
    sid = session["session_id"]
    result = _evaluate(sid)
    assert result.get("success") is False, f"no-op trajectory should fail: {result}"
