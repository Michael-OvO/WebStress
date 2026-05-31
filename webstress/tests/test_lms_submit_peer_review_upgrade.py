"""Solvability proof for the upgraded lms_submit_peer_review task.

Confirms the (now harder) "redo the returned peer review" task:
  * is achievable past the real backend gate (POST /peer-reviews/{id}/submit),
  * passes the canonical_diff evaluator when the agent submits the RETURNED
    review with all three rubric criteria scored, the previously-recorded
    criteria changed, and a >=80-char comment that names the reviewee, and
  * fails on representative near-miss trajectories (wrong review, unchanged
    previous scores, too-short comment, comment missing the reviewee name).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_submit_peer_review"


def _create_session(client: TestClient) -> str:
    resp = client.post(f"/api/env/lms/session", json={"task_id": TASK_ID, "seed": 42})
    assert resp.status_code == 200, resp.text
    return resp.json()["session_id"]


def _targets_and_state(session_id: str):
    sm = app.state.session_manager
    targets = dict(sm.get_targets(session_id))
    state = sm.get_state(session_id)
    return targets, state


def _good_comment(reviewee_name: str) -> str:
    base = (
        f"Hi {reviewee_name}, your submission states its thesis clearly, but the "
        f"middle section needs deeper analysis and at least one original example "
        f"to support the central claim before resubmission."
    )
    assert len(base.strip()) >= 80
    assert reviewee_name.lower() in base.lower()
    return base


def _changed_scores(targets: dict) -> dict:
    """Scores that differ from the previously-recorded clarity/depth values."""
    prev_clarity = int(targets["prev_clarity"]) if targets.get("prev_clarity") else 0
    prev_depth = int(targets["prev_depth"]) if targets.get("prev_depth") else 0
    clarity = 5 if prev_clarity != 5 else 4
    depth = 5 if prev_depth != 5 else 4
    return {"clarity": clarity, "depth": depth, "originality": 4}


def test_correct_trajectory_passes():
    client = TestClient(app)
    session_id = _create_session(client)
    targets, _ = _targets_and_state(session_id)

    review_id = targets["target_review_id"]
    scores = _changed_scores(targets)
    comment = _good_comment(targets["reviewee_name"])

    resp = client.post(
        f"/api/env/lms/peer-reviews/{review_id}/submit",
        json={"session_id": session_id, "rubric_scores": scores, "comments": comment},
    )
    assert resp.status_code == 200, resp.text

    # Re-read post-mutation state + targets from the app's session manager.
    targets, state = _targets_and_state(session_id)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than the legacy 2-check eval: positive update + many invariants.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_review_fails():
    """Submitting a DIFFERENT (assigned) review instead of the returned one fails."""
    client = TestClient(app)
    session_id = _create_session(client)
    targets, state = _targets_and_state(session_id)

    target_id = targets["target_review_id"]
    other = next(
        (r for r in state.peer_reviews if r.id != target_id and r.status != "submitted"),
        None,
    )
    assert other is not None, "expected another non-submitted review to exist"

    comment = _good_comment(targets["reviewee_name"])
    resp = client.post(
        f"/api/env/lms/peer-reviews/{other.id}/submit",
        json={"session_id": session_id, "rubric_scores": {"clarity": 5, "depth": 5, "originality": 4}, "comments": comment},
    )
    assert resp.status_code == 200, resp.text

    targets, state = _targets_and_state(session_id)
    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected failure, got {result}"


def test_unchanged_previous_scores_fails():
    """Submitting the returned review but keeping the previous clarity/depth fails."""
    client = TestClient(app)
    session_id = _create_session(client)
    targets, _ = _targets_and_state(session_id)

    review_id = targets["target_review_id"]
    # Reuse the exact previously-recorded clarity & depth -> must NOT pass.
    prev_clarity = int(targets["prev_clarity"])
    prev_depth = int(targets["prev_depth"])
    scores = {"clarity": prev_clarity, "depth": prev_depth, "originality": 4}
    comment = _good_comment(targets["reviewee_name"])

    resp = client.post(
        f"/api/env/lms/peer-reviews/{review_id}/submit",
        json={"session_id": session_id, "rubric_scores": scores, "comments": comment},
    )
    assert resp.status_code == 200, resp.text

    targets, state = _targets_and_state(session_id)
    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected failure, got {result}"


def test_short_comment_fails():
    """A comment shorter than 80 chars fails even with correct scores."""
    client = TestClient(app)
    session_id = _create_session(client)
    targets, _ = _targets_and_state(session_id)

    review_id = targets["target_review_id"]
    scores = _changed_scores(targets)
    short = f"{targets['reviewee_name']}: good work overall."  # < 80 chars
    assert len(short.strip()) < 80

    resp = client.post(
        f"/api/env/lms/peer-reviews/{review_id}/submit",
        json={"session_id": session_id, "rubric_scores": scores, "comments": short},
    )
    assert resp.status_code == 200, resp.text

    targets, state = _targets_and_state(session_id)
    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected failure, got {result}"


def test_comment_missing_reviewee_name_fails():
    """A long comment that never names the reviewee fails the comment predicate."""
    client = TestClient(app)
    session_id = _create_session(client)
    targets, _ = _targets_and_state(session_id)

    review_id = targets["target_review_id"]
    scores = _changed_scores(targets)
    comment = (
        "The submission states its thesis clearly, but the middle section needs "
        "deeper analysis and at least one original example to support the central claim."
    )
    assert len(comment.strip()) >= 80
    assert targets["reviewee_name"].lower() not in comment.lower()

    resp = client.post(
        f"/api/env/lms/peer-reviews/{review_id}/submit",
        json={"session_id": session_id, "rubric_scores": scores, "comments": comment},
    )
    assert resp.status_code == 200, resp.text

    targets, state = _targets_and_state(session_id)
    result = evaluate(task=get_task(TASK_ID), server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected failure, got {result}"
