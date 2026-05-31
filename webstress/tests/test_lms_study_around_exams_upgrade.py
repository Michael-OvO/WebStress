"""Solvability proof for the hardened lms_study_around_exams task.

The task was upgraded from medium → hard. The agent must:
  1. Read the calendar and identify which courses have an exam in the next 14 days.
  2. Mark ONLY the UNREAD announcements in those exam-courses as read.
  3. Leave every other announcement untouched (unread announcements in
     non-exam courses, and already-read announcements everywhere).

The discriminator (unread AND course-has-upcoming-exam) is precomputed in the
seed builder and exposed as the scalar target ``unread_in_exam_courses_ids``.
The correct solution sweeps exactly that set via the real backend endpoint.

Modeled on test_pp_immunization_end_to_end.py.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _create_session() -> tuple[SessionManager, str, dict, object]:
    sm = SessionManager()
    sid, targets, _ = sm.create_session(
        env_id="lms",
        task_id="lms_study_around_exams",
        seed=42,
    )
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def test_seed_answer_set_is_non_trivial() -> None:
    """The exam-course unread set must be a STRICT subset of all unread.

    This is what makes the task hard: a naive "mark every unread announcement
    as read" agent would touch the non-exam-course decoys and fail.
    """
    _sm, _sid, targets, state = _create_session()
    all_unread = [a for a in targets["unread_announcement_ids"].split(",") if a]
    exam_unread = [a for a in targets["unread_in_exam_courses_ids"].split(",") if a]
    non_exam_unread = [
        a for a in targets["unread_in_non_exam_courses_ids"].split(",") if a
    ]

    assert exam_unread, "expected at least one unread announcement in an exam course"
    assert non_exam_unread, (
        "expected unread DECOY announcements in non-exam courses — "
        "otherwise the task degenerates to 'mark all unread'"
    )
    # exam-unread is a strict subset of all-unread
    assert set(exam_unread) < set(all_unread)
    # The two partitions are disjoint and together cover all unread.
    assert set(exam_unread).isdisjoint(set(non_exam_unread))
    assert set(exam_unread) | set(non_exam_unread) == set(all_unread)
    assert int(targets["unread_in_exam_courses_count"]) == len(exam_unread)

    # Recompute the discriminator independently from raw state to prove the
    # builder's intersection is the genuine (unread ∧ exam-course) set.
    exam_courses = {c for c in targets["courses_with_upcoming_exams"].split(",") if c}
    recomputed = sorted(
        a.id
        for a in state.announcements
        if (not a.is_read) and a.course_id in exam_courses
    )
    assert recomputed == sorted(exam_unread)


def test_correct_trajectory_via_endpoint_passes() -> None:
    """Driving the real mark-read endpoint over the exam-course unread set passes."""
    sm, sid, targets, state = _create_session()
    app.state.session_manager = sm
    client = TestClient(app)

    exam_unread = [a for a in targets["unread_in_exam_courses_ids"].split(",") if a]
    assert exam_unread

    for ann_id in exam_unread:
        resp = client.post(
            f"/api/env/lms/announcements/{ann_id}/read",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["announcement"]["is_read"] is True

    task = get_task("lms_study_around_exams")
    result = evaluate(
        task=task,
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than a 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_mark_all_unread_fails() -> None:
    """The most tempting wrong solution — mark EVERY unread announcement — fails.

    Marking the non-exam-course unread decoys trips the critical
    'did not mark unread announcements read in courses without an upcoming exam'
    invariant and the exact-count constraint.
    """
    sm, sid, targets, state = _create_session()
    app.state.session_manager = sm
    client = TestClient(app)

    # mark_all_read endpoint flips every unread announcement (exam + non-exam).
    resp = client.post(
        "/api/env/lms/announcements/mark_all_read",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    task = get_task("lms_study_around_exams")
    result = evaluate(
        task=task,
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, (
        f"mark-all-unread should fail the discriminator but passed: {result}"
    )


def test_partial_miss_fails() -> None:
    """Marking only SOME of the exam-course unread set is a near-miss that fails."""
    sm, sid, targets, state = _create_session()
    app.state.session_manager = sm
    client = TestClient(app)

    exam_unread = [a for a in targets["unread_in_exam_courses_ids"].split(",") if a]
    assert len(exam_unread) >= 2, "need >=2 to drop one and still have a positive"

    # Mark all but the last exam-course unread announcement.
    for ann_id in exam_unread[:-1]:
        resp = client.post(
            f"/api/env/lms/announcements/{ann_id}/read",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text

    task = get_task("lms_study_around_exams")
    result = evaluate(
        task=task,
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, (
        f"incomplete sweep should fail bijection saturation: {result}"
    )
