"""Solvability + near-miss proof for the upgraded lms_compare_course_grades task.

The task (hardened to `hard`) seeds five enrolled courses. The agent must
recompute the displayed weighted grades of exactly the two NAMED courses
(course_code_a / course_code_b), and — because their grades differ by more than
3.00 points — drop the enrollment of the lower-grade named course while leaving
every other enrollment (including lower-grade DECOY courses that are not part of
the named pair) untouched.

The correct solution is driven through the REAL backend drop endpoint
(`webstress.backend.routes.lms.drop_course`), which enforces the live guards
(enrollment must be 'enrolled', drop deadline not passed). Targets returned by
the seed builder are the intended answer, so a passing eval confirms the task is
achievable past the real backend gate.
"""

from __future__ import annotations

from decimal import Decimal

from webstress.backend.routes.lms import SessionScopedRequest, drop_course
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_compare_course_grades"


def _new_session() -> tuple[SessionManager, str, dict, object]:
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def test_seed_targets_are_internally_consistent() -> None:
    """The named-pair lower must be the lower of the two named courses' DISPLAYED
    weighted grades, the gap must exceed the 3-point tolerance, and the named-pair
    lower enrollment must belong to the named-pair lower course."""
    _sm, _sid, targets, state = _new_session()

    code_to_id = {c.course_code: c.id for c in state.courses}
    id_to_enr = {e.course_id: e.id for e in state.enrollments}
    a_id = code_to_id[targets["course_code_a"]]
    b_id = code_to_id[targets["course_code_b"]]
    score_a = state.weighted_score_for_course(a_id)
    score_b = state.weighted_score_for_course(b_id)
    assert score_a is not None and score_b is not None

    expected_lower = a_id if score_a < score_b else b_id
    assert targets["named_pair_lower_course_id"] == expected_lower
    assert targets["named_pair_lower_enrollment_id"] == id_to_enr[expected_lower]
    # Gap discriminator must be self-consistent.
    gap = abs(score_a - score_b)
    assert Decimal(targets["named_pair_gap"]) == gap.quantize(Decimal("0.01"))
    assert targets["named_pair_gap_above_3"] == ("true" if gap > Decimal("3") else "false")
    # For seed=42 the gap is large, so the DROP branch is the canonical answer.
    assert targets["named_pair_gap_above_3"] == "true"


def test_correct_drop_via_real_endpoint_passes() -> None:
    """Dropping exactly the named-pair lower enrollment through the real backend
    drop endpoint evaluates to a pass."""
    sm, sid, targets, state = _new_session()

    lower_course_id = targets["named_pair_lower_course_id"]
    # Drive the REAL backend mutation (enforces enrolled-status + drop-deadline guards).
    result = drop_course(
        course_id=lower_course_id,
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    assert result["dropped"] is True
    assert result["enrollment"]["id"] == targets["named_pair_lower_enrollment_id"]
    assert result["enrollment"]["status"] == "dropped"

    state = sm.get_state(sid)
    eval_result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert eval_result.get("success") is True, f"result: {eval_result}"
    assert eval_result.get("score", 0.0) >= 0.99


def test_dropping_the_higher_named_course_fails() -> None:
    """Dropping the HIGHER-grade named course (the wrong one of the pair) fails."""
    sm, sid, targets, state = _new_session()

    higher_course_id = targets["named_pair_higher_course_id"]
    drop_course(
        course_id=higher_course_id,
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    state = sm.get_state(sid)
    eval_result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert eval_result.get("success") is False


def test_doing_nothing_fails_when_gap_above_tolerance() -> None:
    """Because the named grades differ by > 3 points, the no-op (tie) branch must
    NOT pass: an agent that drops nothing fails."""
    _sm, sid, targets, state = _new_session()
    eval_result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert eval_result.get("success") is False


def test_dropping_a_decoy_course_in_addition_fails() -> None:
    """Over-acting — dropping the correct named-pair lower AND an extra
    (non-named) enrollment — trips the critical frozen-enrollment invariant."""
    sm, sid, targets, state = _new_session()

    lower_course_id = targets["named_pair_lower_course_id"]
    drop_course(
        course_id=lower_course_id,
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    # Find a different enrolled course (a decoy not in the named pair) and drop it too.
    state = sm.get_state(sid)
    extra = next(
        e for e in state.enrollments
        if e.status == "enrolled"
        and e.id != targets["named_pair_lower_enrollment_id"]
    )
    drop_course(
        course_id=extra.course_id,
        body=SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    state = sm.get_state(sid)
    eval_result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert eval_result.get("success") is False
