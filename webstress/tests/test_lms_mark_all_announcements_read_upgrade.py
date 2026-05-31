"""Solvability proof for the upgraded lms_mark_all_announcements_read task.

The task was re-tiered easy -> medium: instead of a one-shot "mark all read",
the agent must mark ONLY the unread *urgent* announcements as read, leaving every
unread normal-priority announcement (and every already-read announcement)
untouched. The bulk `mark_all_read` shortcut now over-marks the normal-unread
decoys and fails the critical invariant/constraint.

Correct solution is driven through the REAL backend endpoint
POST /announcements/{id}/read via starlette TestClient so the proof also
confirms the action is reachable past all gates.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _make_session() -> tuple[SessionManager, str, dict]:
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="lms",
        task_id="lms_mark_all_announcements_read",
        seed=42,
    )
    return sm, sid, dict(targets)


def test_correct_trajectory_marks_only_unread_urgent_and_passes():
    """Marking exactly the unread-urgent announcements via the real endpoint passes."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    try:
        urgent_ids = [i for i in targets["unread_urgent_announcement_ids"].split(",") if i]
        assert len(urgent_ids) == 3, f"expected 3 unread urgent targets, got {urgent_ids}"

        for ann_id in urgent_ids:
            resp = client.post(
                f"/api/env/lms/announcements/{ann_id}/read",
                json={"session_id": sid},
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["announcement"]["is_read"] is True

        state = sm.get_state(sid)
        # The unread normal decoys must remain unread after the correct trajectory.
        normal_ids = set(
            i for i in targets["unread_normal_announcement_ids"].split(",") if i
        )
        for a in state.announcements:
            if a.id in normal_ids:
                assert a.is_read is False

        result = evaluate(
            task=get_task("lms_mark_all_announcements_read"),
            server_state=state,
            targets=targets,
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"result: {result}"
        # canonical_diff carries the bijection update plus the standing invariant wall.
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2
    finally:
        sm.destroy(sid)


def test_mark_all_read_bulk_shortcut_fails():
    """The naive `mark_all_read` over-marks normal-unread decoys -> evaluation fails."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    try:
        resp = client.post(
            "/api/env/lms/announcements/mark_all_read",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text
        # It marked the 7 unread (3 urgent + 4 normal).
        assert resp.json()["marked_read"] == 7

        state = sm.get_state(sid)
        result = evaluate(
            task=get_task("lms_mark_all_announcements_read"),
            server_state=state,
            targets=targets,
            trajectory=[],
        )
        assert result.get("success") is False, f"bulk shortcut should fail: {result}"
    finally:
        sm.destroy(sid)


def test_partial_miss_one_urgent_fails():
    """Marking only 2 of the 3 unread-urgent announcements fails the bijection."""
    sm, sid, targets = _make_session()
    client = TestClient(app)
    try:
        urgent_ids = [i for i in targets["unread_urgent_announcement_ids"].split(",") if i]
        for ann_id in urgent_ids[:-1]:  # deliberately skip the last urgent one
            resp = client.post(
                f"/api/env/lms/announcements/{ann_id}/read",
                json={"session_id": sid},
            )
            assert resp.status_code == 200, resp.text

        state = sm.get_state(sid)
        result = evaluate(
            task=get_task("lms_mark_all_announcements_read"),
            server_state=state,
            targets=targets,
            trajectory=[],
        )
        assert result.get("success") is False, f"partial should fail: {result}"
    finally:
        sm.destroy(sid)
