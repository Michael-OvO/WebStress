"""Solvability + near-miss proof for the hardened lms_complete_account_audit task.

Drives the CORRECT solution through the REAL LMS backend endpoints
(TestClient over webstress.app), using the seed targets as the intended
answer, then evaluates via the canonical_diff evaluator. Also asserts a
near-miss trajectory fails.

The hardened task requires, in one account-wide orchestration:
  1. mark EVERY unread announcement as read (saturating bijection, 6 rows),
  2. submit EVERY pending peer review with full rubric scores + >=50-char
     comments (bijection over pending_review_ids),
  3. send ONE audit summary to the advisor whose subject states the EXACT
     re-derived unread count and whose body is >= 60 chars,
while leaving every already-read announcement, already-submitted peer
review, and all sibling collections (grades/enrollments critical) untouched.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_complete_account_audit"
ENV = "lms"


def _split(csv: str) -> list[str]:
    return [x.strip() for x in (csv or "").split(",") if x.strip()]


def _new_session() -> tuple[str, dict]:
    """Create a session directly on the app's SessionManager so the
    TestClient routes operate on the SAME state object we later evaluate."""
    sm: SessionManager = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id=ENV, task_id=TASK_ID, seed=42)
    return sid, dict(targets)


def test_correct_trajectory_via_backend_passes():
    """The intended solution, driven through real endpoints, scores >= 0.99."""
    client = TestClient(app)
    sid, targets = _new_session()
    sm: SessionManager = app.state.session_manager
    state = sm.get_state(sid)

    unread_ids = _split(targets["unread_announcement_ids"])
    pending_ids = _split(targets["pending_review_ids"])
    assert unread_ids, "expected unread announcements to mark read"
    assert pending_ids, "expected pending peer reviews to submit"

    # (1) Mark every unread announcement as read via the real route.
    for aid in unread_ids:
        resp = client.post(
            f"/api/env/{ENV}/announcements/{aid}/read",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text

    # (2) Submit every pending peer review with full rubric + substantive comments.
    long_comment = (
        "Clear thesis and solid structure; the analysis is well developed and the "
        "argument shows original, independent thinking throughout the submission."
    )
    assert len(long_comment) >= 50
    for rid in pending_ids:
        resp = client.post(
            f"/api/env/{ENV}/peer-reviews/{rid}/submit",
            json={
                "session_id": sid,
                "rubric_scores": {"clarity": 4, "depth": 5, "originality": 4},
                "comments": long_comment,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["peer_review"]["status"] == "submitted"

    # (3) Send the single audit summary to the advisor; subject states the exact
    #     re-derived unread count; body >= 60 chars.
    unread_count = len(unread_ids)
    body_text = (
        f"Account audit complete: marked {unread_count} unread announcements as read "
        f"and submitted all {len(pending_ids)} pending peer reviews."
    )
    assert len(body_text) >= 60
    resp = client.post(
        f"/api/env/{ENV}/messages/send",
        json={
            "session_id": sid,
            "to": targets["advisor_name"],
            "subject": f"Account audit summary: {unread_count} announcements marked read",
            "body": body_text,
        },
    )
    assert resp.status_code == 200, resp.text

    # Evaluate the real post-state via the canonical_diff evaluator.
    state = sm.get_state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Hardened task has many positive + negative checks (two bijections + 5 constraints).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 4


def test_near_miss_missing_obligations_fails():
    """A near-miss that marks announcements + messages but skips peer reviews,
    and uses a subject WITHOUT the exact unread count, must fail."""
    client = TestClient(app)
    sid, targets = _new_session()
    sm: SessionManager = app.state.session_manager

    unread_ids = _split(targets["unread_announcement_ids"])
    # Mark only SOME announcements (leave one unread) and skip all peer reviews.
    for aid in unread_ids[:-1]:
        resp = client.post(
            f"/api/env/{ENV}/announcements/{aid}/read",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text

    # Send a vague message with no count in the subject.
    resp = client.post(
        f"/api/env/{ENV}/messages/send",
        json={
            "session_id": sid,
            "to": targets["advisor_name"],
            "subject": "Audit done",
            "body": "I finished the audit.",
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
    assert result.get("success") is False, f"near-miss should fail: {result}"


def test_wrong_subject_count_fails():
    """Fully completing announcements + peer reviews but stating the WRONG
    count in the subject must fail the critical message constraint."""
    client = TestClient(app)
    sid, targets = _new_session()
    sm: SessionManager = app.state.session_manager

    unread_ids = _split(targets["unread_announcement_ids"])
    pending_ids = _split(targets["pending_review_ids"])

    for aid in unread_ids:
        client.post(f"/api/env/{ENV}/announcements/{aid}/read", json={"session_id": sid})

    long_comment = (
        "Clear thesis and solid structure; the analysis is well developed and the "
        "argument shows original, independent thinking throughout the submission."
    )
    for rid in pending_ids:
        client.post(
            f"/api/env/{ENV}/peer-reviews/{rid}/submit",
            json={
                "session_id": sid,
                "rubric_scores": {"clarity": 4, "depth": 5, "originality": 4},
                "comments": long_comment,
            },
        )

    wrong_count = len(unread_ids) + 99  # deliberately wrong
    resp = client.post(
        f"/api/env/{ENV}/messages/send",
        json={
            "session_id": sid,
            "to": targets["advisor_name"],
            "subject": f"Account audit summary: {wrong_count} announcements marked read",
            "body": "Account audit complete with all announcements and peer reviews handled fully.",
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
    assert result.get("success") is False, f"wrong-count should fail: {result}"
