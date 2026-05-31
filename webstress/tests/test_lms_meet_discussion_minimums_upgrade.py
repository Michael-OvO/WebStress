"""Solvability proof for the hardened lms_meet_discussion_minimums task.

The upgraded task now requires the agent to:
  * read the TARGET discussion's stated minimums (min_posts=2, min_replies=2),
  * author exactly 2 new top-level posts,
  * post exactly 2 replies, each attached to a DISTINCT existing classmate post,
  * with fresh timestamps (>= session_start),
  * without editing existing posts, posting elsewhere, or sending messages.

The correct solution is driven through the REAL backend endpoints
(``/discussions/{id}/posts`` and ``/discussions/{id}/posts/{pid}/reply``) via a
Starlette ``TestClient`` so the proof confirms the answer is achievable past the
route gates (parent-post existence, author identity stamping, etc.).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_meet_discussion_minimums"
ENV_ID = "lms"


def _create_session():
    """Create a session on the app's shared session_manager (so TestClient
    route mutations land on the same state the evaluator reads)."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id=ENV_ID, task_id=TASK_ID, seed=42)
    return sm, sid, dict(targets)


def test_seed_exposes_harder_minimums_and_distinct_parents() -> None:
    """The hardened seed must produce min_posts=2/min_replies=2 on the target
    discussion and expose two distinct top-level classmate parents to reply to."""
    sm, sid, targets = _create_session()
    try:
        state = sm.get_state(sid)
        assert int(targets["target_min_posts"]) == 2
        assert int(targets["target_min_replies"]) == 2

        parents = [p.strip() for p in targets["reply_parent_post_ids"].split(",") if p.strip()]
        assert len(parents) == 2, f"expected 2 distinct reply parents, got {parents}"
        assert len(set(parents)) == 2, "reply parents must be distinct"

        tid = targets["target_discussion_id"]
        by_id = {p.id: p for p in state.discussion_posts}
        for pid in parents:
            post = by_id[pid]
            assert post.discussion_id == tid, "reply parent must live in the target discussion"
            assert post.parent_post_id is None, "reply parent must be a top-level post"
            assert post.author_id != state.student.id, "reply parent must be a classmate post"

        disc = state.get_discussion(tid)
        assert disc.min_posts == 2 and disc.min_replies == 2
    finally:
        sm.destroy(sid)


def test_correct_trajectory_via_backend_passes() -> None:
    """Driving the intended solution through the real endpoints passes evaluate()."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    try:
        tid = targets["target_discussion_id"]
        parents = [p.strip() for p in targets["reply_parent_post_ids"].split(",") if p.strip()]

        # 2 new top-level posts (matches min_posts).
        for i in range(int(targets["target_min_posts"])):
            resp = client.post(
                f"/api/env/lms/discussions/{tid}/posts",
                json={"session_id": sid, "body": f"My analysis number {i + 1} of this topic."},
            )
            assert resp.status_code == 200, resp.text

        # 2 replies, each to a DISTINCT existing classmate post (matches min_replies).
        for i, parent_id in enumerate(parents):
            resp = client.post(
                f"/api/env/lms/discussions/{tid}/posts/{parent_id}/reply",
                json={"session_id": sid, "body": f"Building on your point, reply {i + 1}."},
            )
            assert resp.status_code == 200, resp.text

        state = sm.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
        # canonical_diff coverage is rich: 3 create checks + invariants + constraints.
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 5
    finally:
        sm.destroy(sid)


def test_reply_to_same_parent_twice_fails() -> None:
    """Near-miss: correct counts but both replies attach to the SAME parent.

    The reply bijection over the two distinct required parents cannot saturate
    (only one parent is covered), so the task must fail."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    try:
        tid = targets["target_discussion_id"]
        parents = [p.strip() for p in targets["reply_parent_post_ids"].split(",") if p.strip()]

        for i in range(int(targets["target_min_posts"])):
            resp = client.post(
                f"/api/env/lms/discussions/{tid}/posts",
                json={"session_id": sid, "body": f"Post {i + 1}."},
            )
            assert resp.status_code == 200, resp.text

        # Both replies target the SAME (first) parent — distinctness violated.
        for i in range(int(targets["target_min_replies"])):
            resp = client.post(
                f"/api/env/lms/discussions/{tid}/posts/{parents[0]}/reply",
                json={"session_id": sid, "body": f"Same-parent reply {i + 1}."},
            )
            assert resp.status_code == 200, resp.text

        state = sm.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"expected failure, got {result}"
    finally:
        sm.destroy(sid)


def test_only_one_post_one_reply_fails() -> None:
    """Near-miss: the OLD easy answer (1 post + 1 reply) no longer satisfies the
    raised minimums (2 posts + 2 distinct replies)."""
    sm, sid, targets = _create_session()
    client = TestClient(app)
    try:
        tid = targets["target_discussion_id"]
        parents = [p.strip() for p in targets["reply_parent_post_ids"].split(",") if p.strip()]

        resp = client.post(
            f"/api/env/lms/discussions/{tid}/posts",
            json={"session_id": sid, "body": "Only one post."},
        )
        assert resp.status_code == 200, resp.text
        resp = client.post(
            f"/api/env/lms/discussions/{tid}/posts/{parents[0]}/reply",
            json={"session_id": sid, "body": "Only one reply."},
        )
        assert resp.status_code == 200, resp.text

        state = sm.get_state(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"expected failure, got {result}"
    finally:
        sm.destroy(sid)
