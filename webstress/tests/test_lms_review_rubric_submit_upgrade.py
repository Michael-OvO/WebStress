"""Solvability proof for the upgraded lms_review_rubric_submit task.

Confirms the hardened task is achievable through the REAL backend submit
endpoint when the agent picks the highest-rubric-point unsubmitted assignment
in the named course and names the file with that assignment's rubric-criteria
count, and that near-miss trajectories (wrong file_name count, wrong
assignment) fail.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_review_rubric_submit"


def _make_session(seed: int = 42):
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=seed)
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _correct_file_name(targets: dict) -> str:
    return f"rubric_review_{targets['highest_points_rubric_count']}.pdf"


def _evaluate(state, targets):
    return evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )


def test_correct_trajectory_via_backend_passes():
    """Submitting the highest-rubric-point unsubmitted assignment via the real
    endpoint with the rubric-count-named file passes evaluate()."""
    sm, sid, targets, state = _make_session(seed=42)
    client = TestClient(app)

    target_id = targets["highest_points_unsubmitted_id"]
    assert target_id, "seed did not produce a target assignment"
    file_name = _correct_file_name(targets)

    # Sanity: the target is genuinely unsubmitted in the named course.
    before = state.get_assignment(target_id)
    assert before is not None and before.submission_status == "not_submitted"
    course = state.get_course(before.course_id)
    assert course is not None and course.course_code == targets["course_code"]
    # Sanity: it is the argmax-by-points among unsubmitted in that course.
    unsubmitted = [
        a for a in state.assignments
        if a.course_id == before.course_id and a.submission_status == "not_submitted"
    ]
    assert before.points_possible == max(a.points_possible for a in unsubmitted)
    # Sanity: the encoded count matches the actual rubric size.
    assert str(len(before.rubric)) == targets["highest_points_rubric_count"]

    resp = client.post(
        f"/api/env/lms/assignments/{target_id}/submit",
        json={"session_id": sid, "file_name": file_name},
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = _evaluate(state, targets)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
    # Richer than a 2-check legacy eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_file_name_count_fails():
    """Submitting the right assignment but with the wrong rubric-count file fails."""
    sm, sid, targets, state = _make_session(seed=42)
    client = TestClient(app)

    target_id = targets["highest_points_unsubmitted_id"]
    correct = int(targets["highest_points_rubric_count"])
    wrong_file = f"rubric_review_{correct + 1}.pdf"

    resp = client.post(
        f"/api/env/lms/assignments/{target_id}/submit",
        json={"session_id": sid, "file_name": wrong_file},
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = _evaluate(state, targets)
    assert result.get("success") is False, f"expected failure, got: {result}"


def test_wrong_assignment_fails():
    """Submitting a different (decoy, wrong-course) assignment fails: the target
    stays unsubmitted (update unsatisfied) and the decoy invariant is violated."""
    sm, sid, targets, state = _make_session(seed=42)
    client = TestClient(app)

    decoy_id = targets["rubric_review_decoy_id"]
    assert decoy_id, "seed did not produce a decoy"
    file_name = _correct_file_name(targets)

    resp = client.post(
        f"/api/env/lms/assignments/{decoy_id}/submit",
        json={"session_id": sid, "file_name": file_name},
    )
    assert resp.status_code == 200, resp.text

    state = sm.get_state(sid)
    result = _evaluate(state, targets)
    assert result.get("success") is False, f"expected failure, got: {result}"


def test_correct_trajectory_multiple_seeds():
    """The correct strategy passes across several seeds (robustness)."""
    for seed in (7, 123, 999, 2024, 17, 88, 1001):
        sm, sid, targets, state = _make_session(seed=seed)
        client = TestClient(app)
        target_id = targets["highest_points_unsubmitted_id"]
        file_name = _correct_file_name(targets)
        resp = client.post(
            f"/api/env/lms/assignments/{target_id}/submit",
            json={"session_id": sid, "file_name": file_name},
        )
        assert resp.status_code == 200, resp.text
        state = sm.get_state(sid)
        result = _evaluate(state, targets)
        assert result.get("success") is True, f"seed={seed} result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"seed={seed} score: {result}"
