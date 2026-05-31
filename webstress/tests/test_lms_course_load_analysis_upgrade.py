"""Solvability proof for the hardened lms_course_load_analysis task.

The upgraded task removes the leaked branch flag from the instruction. The agent
must RE-DERIVE the projected credit-weighted GPA (current_GPA * total_credits +
2.0*3) / (total_credits + 3) to choose between two branches:

  * projected GPA >= 3.0  -> mark every unread announcement as read (Branch 1)
  * projected GPA <  3.0  -> drop the single lowest-weighted-grade course (Branch 2)

seed=42 produces gpa=3.20 over 13 credit hours, so the projected GPA is
2.975 (< 3.0) and Branch 2 (drop the lowest-performing course) is correct.
seed=30 produces a projected GPA >= 3.0, exercising Branch 1.

Both branches are driven through the REAL LMS backend endpoints
(POST /courses/{id}/drop and POST /announcements/mark_all_read) to confirm the
intended answer is reachable past the server gates.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.models.lms import Enrollment
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


def _session(seed: int):
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="lms", task_id="lms_course_load_analysis", seed=seed,
    )
    return sm, sid, dict(targets)


def test_drop_branch_correct_solution_passes():
    """seed=42 -> projected GPA < 3.0 -> drop lowest-performing course via real route."""
    sm, sid, targets = _session(42)
    # Sanity: this seed exercises the drop branch.
    assert targets["can_add_course"] == "false"

    client = TestClient(app)
    cid = targets["lowest_performing_course_id"]
    resp = client.post(f"/api/env/lms/courses/{cid}/drop", json={"session_id": sid})
    assert resp.status_code == 200, resp.text
    assert resp.json().get("dropped") is True

    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99
    # Hardened task has a thick wall of negative coverage.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_mark_read_branch_correct_solution_passes():
    """seed=30 -> projected GPA >= 3.0 -> mark all unread announcements read via real route."""
    sm, sid, targets = _session(30)
    assert targets["can_add_course"] == "true"

    client = TestClient(app)
    resp = client.post(
        "/api/env/lms/announcements/mark_all_read", json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99


def test_wrong_branch_fails():
    """seed=42 is a drop branch; marking announcements read (wrong branch) must fail."""
    sm, sid, targets = _session(42)
    client = TestClient(app)
    resp = client.post(
        "/api/env/lms/announcements/mark_all_read", json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_wrong_drop_target_fails():
    """Dropping a course other than the lowest-weighted-grade course must fail."""
    sm, sid, targets = _session(42)
    client = TestClient(app)
    state = sm.get_state(sid)
    target_cid = targets["lowest_performing_course_id"]
    wrong_cid = next(
        e.course_id for e in state.enrollments if e.course_id != target_cid
    )
    resp = client.post(
        f"/api/env/lms/courses/{wrong_cid}/drop", json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_collateral_new_enrollment_fails():
    """Correct drop plus a stray new enrollment trips the critical no-new-course constraint."""
    sm, sid, targets = _session(42)
    client = TestClient(app)
    client.post(
        f"/api/env/lms/courses/{targets['lowest_performing_course_id']}/drop",
        json={"session_id": sid},
    )
    state = sm.get_state(sid)
    # Mirror the create_enrollment route (a tempting wrong action) by appending a
    # brand-new enrollment row. The no-new-course constraint must reject this.
    state.enrollments.append(
        Enrollment(
            id="enrollment_collateral",
            student_id=state.student.id,
            course_id="course_2",
            role="student",
            status="enrolled",
        )
    )

    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_noop_fails():
    """Doing nothing fails both branches."""
    sm, sid, targets = _session(42)
    task = get_task("lms_course_load_analysis")
    result = evaluate(
        task=task,
        server_state=sm.get_state(sid),
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
