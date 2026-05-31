"""Solvability + near-miss proof for the hardened lms_peer_review_cycle task.

Drives the CORRECT solution through the REAL backend endpoint handler
(``submit_peer_review``) using a real ``SessionManager`` instance, then evaluates
via the canonical_diff evaluator. Also asserts several near-miss trajectories fail.

Method note: the correct path calls the route handler ``submit_peer_review`` directly
(passing the same SessionManager the session was created on). This is the real
backend mutation — identical to the HTTP path — and avoids the dual session-manager
problem of TestClient (which owns a separate manager via FastAPI dependency
injection). All backend gates (status guard, score sanitisation) are exercised.
"""

from __future__ import annotations

from webstress.backend.state import SessionManager
from webstress.backend.routes.lms import SubmitPeerReviewRequest, submit_peer_review
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_peer_review_cycle"
RUBRIC = ["clarity", "depth", "originality"]


def _make_session():
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _pending_ids(targets: dict) -> list[str]:
    return [r.strip() for r in targets["pending_review_ids"].split(",") if r.strip()]


def _correct_scores_for(review) -> dict[str, int]:
    """All three criteria 1-5; for each previously-scored criterion choose a
    strictly-higher value than the prior score (re-derived from prior feedback)."""
    scores = {"clarity": 4, "depth": 4, "originality": 4}
    for crit, prev in dict(review.previous_rubric_scores).items():
        scores[crit] = min(5, int(prev) + 1)
    return scores


def _correct_comment_for(review) -> str:
    first_name = str(review.reviewee_name).split()[0]
    return (
        f"{first_name}, your clarity and depth are strong; tighten the "
        f"originality of the conclusion to lift the overall argument."
    )


def _submit(sm, sid, review_id, scores, comments):
    body = SubmitPeerReviewRequest(session_id=sid, rubric_scores=scores, comments=comments)
    return submit_peer_review(review_id=review_id, body=body, session_manager=sm)


def _evaluate(state, targets):
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )


# ---------------------------------------------------------------------------
# Positive: correct trajectory passes
# ---------------------------------------------------------------------------

def test_correct_trajectory_passes():
    sm, sid, targets, state = _make_session()
    pending = _pending_ids(targets)
    assert len(pending) >= 3, f"expected a non-trivial pending set, got {pending}"

    # Sanity: at least one returned-for-revision review carries prior scores.
    returned = [r for r in state.peer_reviews if r.id in pending and r.previous_rubric_scores]
    assert returned, "expected >=1 returned review with previous_rubric_scores"

    by_id = {r.id: r for r in state.peer_reviews}
    for rid in pending:
        review = by_id[rid]
        _submit(sm, sid, rid, _correct_scores_for(review), _correct_comment_for(review))

    result = _evaluate(state, targets)
    assert result.get("success") is True, f"reasoning:\n{result.get('reasoning')}"
    assert result.get("score", 0.0) >= 0.99, result.get("score")
    # canonical_diff is richer than a 2-check legacy eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


# ---------------------------------------------------------------------------
# Near-miss A: ignores prior feedback (does not raise above previous scores)
# ---------------------------------------------------------------------------

def test_wrong_ignores_prior_feedback_fails():
    sm, sid, targets, state = _make_session()
    pending = _pending_ids(targets)
    by_id = {r.id: r for r in state.peer_reviews}
    for rid in pending:
        review = by_id[rid]
        # All three criteria present and in-range, but for returned reviews we
        # re-use the previous (too-low) scores instead of raising them.
        scores = {"clarity": 4, "depth": 4, "originality": 4}
        for crit, prev in dict(review.previous_rubric_scores).items():
            scores[crit] = int(prev)  # NOT strictly higher
        _submit(sm, sid, rid, scores, _correct_comment_for(review))

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"reasoning:\n{result.get('reasoning')}"


# ---------------------------------------------------------------------------
# Near-miss B: generic comment not addressed to the reviewee
# ---------------------------------------------------------------------------

def test_wrong_generic_comment_fails():
    sm, sid, targets, state = _make_session()
    pending = _pending_ids(targets)
    by_id = {r.id: r for r in state.peer_reviews}
    generic = (
        "Good clarity overall; please expand the depth of the analysis "
        "and add more originality in the conclusion section."
    )
    for rid in pending:
        review = by_id[rid]
        _submit(sm, sid, rid, _correct_scores_for(review), generic)

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"reasoning:\n{result.get('reasoning')}"


# ---------------------------------------------------------------------------
# Near-miss C: missing a criterion (only two rubric scores)
# ---------------------------------------------------------------------------

def test_wrong_missing_criterion_fails():
    sm, sid, targets, state = _make_session()
    pending = _pending_ids(targets)
    by_id = {r.id: r for r in state.peer_reviews}
    for rid in pending:
        review = by_id[rid]
        scores = _correct_scores_for(review)
        scores.pop("originality", None)  # drop a criterion
        _submit(sm, sid, rid, scores, _correct_comment_for(review))

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"reasoning:\n{result.get('reasoning')}"


# ---------------------------------------------------------------------------
# Near-miss D: leaves one pending review unsubmitted
# ---------------------------------------------------------------------------

def test_wrong_incomplete_fails():
    sm, sid, targets, state = _make_session()
    pending = _pending_ids(targets)
    by_id = {r.id: r for r in state.peer_reviews}
    for rid in pending[:-1]:  # skip the last pending review
        review = by_id[rid]
        _submit(sm, sid, rid, _correct_scores_for(review), _correct_comment_for(review))

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"reasoning:\n{result.get('reasoning')}"
