"""Solvability proof for the hardened lms_ta_dual_role task.

Drives the CORRECT solution through the REAL LMS backend endpoints
(starlette TestClient) so we confirm the intended answer is achievable
past every server gate, then evaluates the resulting state through the
canonical_diff evaluator. A near-miss trajectory is asserted to fail.

Hardening levers exercised here:
  * 5 pending peer reviews (saturating bijection, up from 3).
  * 2 of them are returned-for-revision: every previously-scored rubric
    criterion must be re-scored STRICTLY higher (a per-review discriminator
    the agent must re-derive from previous_rubric_scores).
  * Comments must be >= 60 chars AND name the reviewee.
  * Assignment submit must be exactly one attempt with a fresh timestamp.
  * Exact-cardinality critical constraint: no more / no fewer reviews.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.runner import controller_headers, ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_ta_dual_role"
ENV = "lms"
SEED = 42


@pytest.fixture()
def client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _create_session(client: TestClient) -> tuple[str, dict]:
    resp = client.post(
        f"/api/env/{ENV}/session",
        json={"task_id": TASK_ID, "seed": SEED},
        headers=controller_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["session_id"], data["resolved_targets"]


def _parse_min_scores(targets: dict) -> dict[str, list[int]]:
    """rid -> [min_clarity, min_depth, min_originality]."""
    out: dict[str, list[int]] = {}
    raw = targets.get("pending_review_min_scores", "")
    for entry in [e for e in raw.split("|") if e]:
        rid, mins = entry.split("=", 1)
        out[rid] = [int(s) for s in mins.split("~")]
    return out


def _parse_name_tokens(targets: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    raw = targets.get("pending_review_name_tokens", "")
    for entry in [e for e in raw.split("|") if e]:
        rid, name = entry.split(":", 1)
        out[rid] = name
    return out


def _correct_scores(mins: list[int]) -> dict[str, int]:
    """Pick a valid 1..5 score per criterion that satisfies the minimum."""
    crit = ["clarity", "depth", "originality"]
    return {c: min(max(mins[i], 1), 5) for i, c in enumerate(crit)}


def _comment_for(name: str) -> str:
    base = (
        f"Strong submission overall, {name}. Your analysis is clear and the "
        f"argument is well organized; tighten the conclusion next time."
    )
    assert len(base) >= 60
    return base


def test_correct_trajectory_passes(client: TestClient):
    sid, targets = _create_session(client)
    pending = [r for r in targets["pending_review_ids"].split(",") if r]
    assert len(pending) == 5
    mins = _parse_min_scores(targets)
    names = _parse_name_tokens(targets)

    # (1) Complete every pending peer review through the real endpoint.
    for rid in pending:
        scores = _correct_scores(mins[rid])
        resp = client.post(
            f"/api/env/{ENV}/peer-reviews/{rid}/submit",
            json={
                "session_id": sid,
                "rubric_scores": scores,
                "comments": _comment_for(names[rid]),
            },
        )
        assert resp.status_code == 200, resp.text

    # (2) Submit the student-course assignment exactly once with the file.
    resp = client.post(
        f"/api/env/{ENV}/assignments/{targets['target_assignment_id']}/submit",
        json={"session_id": sid, "file_name": "ta_student_submission.pdf"},
    )
    assert resp.status_code == 200, resp.text

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"


def test_returned_review_not_improved_fails(client: TestClient):
    """Near-miss: leaving a returned review's previous scores un-improved fails."""
    sid, targets = _create_session(client)
    pending = [r for r in targets["pending_review_ids"].split(",") if r]
    mins = _parse_min_scores(targets)
    names = _parse_name_tokens(targets)

    # Find a returned-for-revision review (min on some criterion is > 1).
    returned_rid = next(r for r in pending if max(mins[r]) > 1)

    for rid in pending:
        if rid == returned_rid:
            # Submit minimal valid 1..5 scores WITHOUT improving (score=1 on
            # criteria whose required minimum is above 1).
            scores = {"clarity": 1, "depth": 1, "originality": 1}
        else:
            scores = _correct_scores(mins[rid])
        client.post(
            f"/api/env/{ENV}/peer-reviews/{rid}/submit",
            json={
                "session_id": sid,
                "rubric_scores": scores,
                "comments": _comment_for(names[rid]),
            },
        )

    client.post(
        f"/api/env/{ENV}/assignments/{targets['target_assignment_id']}/submit",
        json={"session_id": sid, "file_name": "ta_student_submission.pdf"},
    )

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"should fail but passed: {result}"


def test_missing_reviewee_name_fails(client: TestClient):
    """Near-miss: a comment that omits the reviewee's name fails the comment guard."""
    sid, targets = _create_session(client)
    pending = [r for r in targets["pending_review_ids"].split(",") if r]
    mins = _parse_min_scores(targets)
    names = _parse_name_tokens(targets)

    for idx, rid in enumerate(pending):
        scores = _correct_scores(mins[rid])
        if idx == 0:
            # Long enough but does not mention the reviewee by name.
            comment = (
                "Overall this is a solid piece of work with a clear thesis and "
                "well-supported reasoning throughout the submission."
            )
        else:
            comment = _comment_for(names[rid])
        client.post(
            f"/api/env/{ENV}/peer-reviews/{rid}/submit",
            json={"session_id": sid, "rubric_scores": scores, "comments": comment},
        )

    client.post(
        f"/api/env/{ENV}/assignments/{targets['target_assignment_id']}/submit",
        json={"session_id": sid, "file_name": "ta_student_submission.pdf"},
    )

    state = app.state.session_manager.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"should fail but passed: {result}"
