"""Solvability proof for the hardened lms_peer_review_redo task.

The upgraded task requires the agent to:
  * redo EVERY returned-for-revision peer review (a saturating bijection over
    target['returned_review_ids']), not just one;
  * apply a deterministic published scoring rule (previous score + 2 capped at
    5; criteria with no previous score -> 3) so the rubric_scores must match the
    exact seed-computed integers, not "any 1-5";
  * write a fresh comment of at least 80 characters;
  * leave every non-returned peer review (and all sibling collections) frozen,
    and send no messages.

The correct solution is driven through the REAL backend endpoint
``POST /api/env/lms/peer-reviews/{id}/submit`` via the Starlette TestClient
against the app's shared SessionManager, confirming the answer is achievable
past the real route (key sanitization + status guard). A near-miss trajectory
(scores that ignore the rule) must fail.
"""

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_peer_review_redo"
SEED = 42


def _create_session():
    """Create a session on the app's shared SessionManager and return ids+targets."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=SEED)
    return sm, sid, dict(targets)


def _returned_ids(targets: dict) -> list[str]:
    raw = targets["returned_review_ids"]
    return [r.strip() for r in raw.split(",") if r.strip()]


def _rule_scores(targets: dict) -> dict[str, int]:
    return {
        "clarity": int(targets["req_score_clarity"]),
        "depth": int(targets["req_score_depth"]),
        "originality": int(targets["req_score_originality"]),
    }


GOOD_COMMENT = (
    "Reworked this review: the thesis is now clearly stated, the supporting "
    "evidence is developed in depth, and the closing reflection is original."
)


def test_seed_shape_is_as_designed():
    """Three returned reviews, all sharing the rule-derived target scores."""
    sm, sid, targets = _create_session()
    state = sm.get_state(sid)

    returned = _returned_ids(targets)
    assert len(returned) == 3, f"expected 3 returned reviews, got {returned}"

    # Rule check: previous {clarity:2, depth:1, originality:absent}
    # -> clarity 4, depth 3, originality 3.
    scores = _rule_scores(targets)
    assert scores == {"clarity": 4, "depth": 3, "originality": 3}, scores

    returned_set = set(returned)
    seen_returned = {r.id for r in state.peer_reviews if r.returned_for_revision}
    assert seen_returned == returned_set
    # There must be non-returned reviews to freeze (the standing wall).
    non_returned = [r for r in state.peer_reviews if not r.returned_for_revision]
    assert len(non_returned) >= 2


def test_correct_trajectory_via_backend_passes():
    """Submitting every returned review with the rule-derived scores passes."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    scores = _rule_scores(targets)

    for review_id in _returned_ids(targets):
        resp = client.post(
            f"/api/env/lms/peer-reviews/{review_id}/submit",
            json={
                "session_id": sid,
                "rubric_scores": scores,
                "comments": GOOD_COMMENT,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["peer_review"]
        assert body["status"] == "submitted"
        assert body["rubric_scores"] == scores

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than the legacy 2-check eval (bijection + standing-wall invariants).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_scores_fail():
    """Ignoring the scoring rule (all 5s) must fail the exact-value predicate."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    wrong_scores = {"clarity": 5, "depth": 5, "originality": 5}

    for review_id in _returned_ids(targets):
        resp = client.post(
            f"/api/env/lms/peer-reviews/{review_id}/submit",
            json={
                "session_id": sid,
                "rubric_scores": wrong_scores,
                "comments": GOOD_COMMENT,
            },
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"wrong scores should fail: {result}"


def test_incomplete_only_one_review_fails():
    """Redoing only one of three returned reviews must fail the bijection."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    scores = _rule_scores(targets)

    only = _returned_ids(targets)[0]
    resp = client.post(
        f"/api/env/lms/peer-reviews/{only}/submit",
        json={
            "session_id": sid,
            "rubric_scores": scores,
            "comments": GOOD_COMMENT,
        },
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"partial redo should fail: {result}"


def test_short_comment_fails():
    """Correct scores but a too-short comment (<80 chars) must fail."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    scores = _rule_scores(targets)
    short = "Looks fine now."

    for review_id in _returned_ids(targets):
        resp = client.post(
            f"/api/env/lms/peer-reviews/{review_id}/submit",
            json={
                "session_id": sid,
                "rubric_scores": scores,
                "comments": short,
            },
        )
        assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"short comment should fail: {result}"
