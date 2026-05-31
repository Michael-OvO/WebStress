"""Solvability + discrimination proof for the hardened lms_exam_conflict task.

The upgraded task stages final exams across DISTINCT calendar days so the agent
must (1) isolate the single most-conflicted exam date (a 3-course cluster, larger
than a 2-course decoy cluster on another day), (2) compute the current weighted
grade for each course in that cluster, and (3) drop the one course holding the
strictly-lowest weighted grade — while leaving the higher-grade conflicting
course, the decoy-cluster courses, and every other enrollment untouched.

The correct trajectory is driven through the REAL backend mutation
(``drop_course`` route handler) using the seed ``targets`` as the intended
answer, which exercises the route's enrollment-status + drop-deadline gates.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

import pytest

from webstress.backend.routes.lms import SessionScopedRequest, drop_course
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_exam_conflict"
SEED = 42


def _make_session():
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=SEED)
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _weighted_score(state, course_id: str) -> Decimal:
    """Re-derive a course's current weighted grade from the gradebook,
    independent of the seed builder, to prove the discriminator is real."""
    course = next(c for c in state.courses if c.id == course_id)
    gp = course.syllabus.grading_policy
    graded_weight = Decimal("0")
    weighted_sum = Decimal("0")
    for cat, policy in gp.items():
        weight = Decimal(str(policy.weight))
        cat_grades = [
            g for g in state.grades
            if g.course_id == course_id
            and g.weight_category == cat
            and g.score is not None
            and not g.is_dropped
        ]
        if not cat_grades:
            continue
        total = sum(
            (Decimal(str(g.score)) / Decimal(str(g.points_possible))) * Decimal("100")
            for g in cat_grades
        )
        avg = (total / Decimal(len(cat_grades))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        graded_weight += weight
        weighted_sum += avg * weight
    if graded_weight > 0:
        return (weighted_sum / graded_weight).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    return Decimal("0")


def test_seed_structure_is_a_genuine_multi_cluster_discriminator():
    """The hardened seed must produce a 3-course primary cluster on the unique
    most-conflicted day, a strictly-unique lowest weighted grade in it, and a
    smaller decoy cluster that is NOT the answer."""
    _sm, _sid, targets, state = _make_session()

    primary = targets["primary_conflict_course_ids"].split(",")
    decoy = targets["decoy_conflict_course_ids"].split(",")
    low = targets["lower_grade_conflict_course_id"]
    high = targets["higher_grade_conflict_course_id"]

    assert len(primary) == 3, "primary conflict cluster should hold 3 courses"
    assert len(decoy) == 2, "decoy conflict cluster should hold 2 courses"
    assert len(primary) > len(decoy), "primary cluster must dominate the decoy cluster"
    assert set(primary).isdisjoint(set(decoy)), "clusters must be disjoint"
    assert low in primary and high in primary

    # The primary day must be the unique calendar date with the most exams.
    from collections import Counter

    day_counts: Counter[str] = Counter()
    for ev in state.calendar_events:
        if ev.event_type == "exam":
            day_counts[str(ev.start_datetime)[:10]] += 1
    max_count = max(day_counts.values())
    assert list(day_counts.values()).count(max_count) == 1, "max exam day must be unique"
    assert day_counts[targets["primary_conflict_day"]] == max_count == 3

    # The lowest weighted grade among the primary cluster must be STRICTLY unique
    # and must match the seed's nominated drop target.
    scores = {cid: _weighted_score(state, cid) for cid in primary}
    min_score = min(scores.values())
    assert sum(1 for v in scores.values() if v == min_score) == 1, "min must be unique"
    assert scores[low] == min_score
    assert scores[high] == max(scores.values())


def test_correct_drop_via_real_route_passes():
    """Dropping the lowest-weighted-grade course in the primary cluster, through
    the real drop_course endpoint, evaluates to a full pass."""
    sm, sid, targets, state = _make_session()

    response = drop_course(
        targets["lower_grade_conflict_course_id"],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    assert response["dropped"] is True

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99
    # Richer than the legacy 2-check eval (one positive update + many negatives).
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


@pytest.mark.parametrize(
    "wrong_key, label",
    [
        ("higher_grade_conflict_course_id", "highest-grade in cluster"),
        ("second_lowest_primary_conflict_course_id", "second-lowest in cluster"),
    ],
)
def test_wrong_course_in_cluster_fails(wrong_key, label):
    """Dropping any conflicting course other than the strict minimum fails."""
    sm, sid, targets, state = _make_session()
    drop_course(
        targets[wrong_key],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"{label} should fail: {result}"


def test_dropping_decoy_cluster_course_fails():
    """The decoy cluster (smaller, wrong day) must never be the answer."""
    sm, sid, targets, state = _make_session()
    drop_course(
        targets["decoy_conflict_course_ids"].split(",")[0],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False


def test_over_action_dropping_two_courses_fails():
    """Dropping the correct course PLUS a decoy violates the
    exactly-one-dropped critical constraint."""
    sm, sid, targets, state = _make_session()
    drop_course(
        targets["lower_grade_conflict_course_id"],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    drop_course(
        targets["decoy_conflict_course_ids"].split(",")[0],
        SessionScopedRequest(session_id=sid),
        session_manager=sm,
    )
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False


def test_do_nothing_fails():
    """The task requires an actual mutation; a no-op trajectory fails."""
    _sm, _sid, targets, state = _make_session()
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False
