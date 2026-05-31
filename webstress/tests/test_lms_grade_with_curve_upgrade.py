"""Solvability + adversarial proof for the upgraded ``lms_grade_with_curve``.

The upgraded task is a two-branch ``oneof`` gated on a seed-computed
discriminator the agent must re-derive (does +5 on the midterm cross a letter
band for the target course?).

* Branch 1 (curve RAISES the letter grade): acknowledge by marking EVERY
  unread announcement that belongs to the target course as read — a saturating
  bijection over the precomputed ``unread_target_announcement_ids`` set, with
  other-course unread announcements present as distractors that must NOT be
  touched (so a blunt ``mark_all_read`` fails).
* Branch 2 (curve does NOT change the letter grade): submit
  ``curve_appeal.pdf`` to the midterm assignment with tightened field
  predicates (exact file name, freshness, attempt increment).

All correct solutions are driven through the REAL LMS backend route handlers
(``mark_announcement_read`` / ``submit_assignment``) so the proof confirms the
intended answer is achievable past every server gate.

Canonical eval seed is 42 (validate stage 3 + variant integrity both use it),
which lands on Branch 1. Branch 2 is proven on seed 2, where the discriminator
is ``false`` and the target-course midterm is submittable.
"""

from __future__ import annotations

from webstress.backend.routes.lms import (
    SessionScopedRequest,
    SubmitAssignmentRequest,
    mark_all_announcements_read,
    mark_announcement_read,
    submit_assignment,
)
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_grade_with_curve"


def _session(seed: int):
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=seed)
    return sm, sid, dict(targets), sm.get_state(sid)


# ── Branch 1: curve raises the letter grade (canonical seed 42) ──────────────

def test_branch1_correct_marks_target_unread_passes():
    """Marking exactly the target-course unread announcements via the real
    endpoint passes with full score."""
    sm, sid, t, state = _session(42)
    assert t["curve_changes_letter"] == "true", "seed 42 must select Branch 1"
    target_ids = [a for a in t["unread_target_announcement_ids"].split(",") if a]
    assert len(target_ids) >= 2, "task should require marking a multi-element set"

    for aid in target_ids:
        mark_announcement_read(
            aid, SessionScopedRequest(session_id=sid), session_manager=sm
        )

    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99
    # canonical_diff coverage is richer than the legacy 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_branch1_partial_marking_fails():
    """Marking only a strict subset of the target-course unread set fails the
    saturating bijection (cardinality)."""
    sm, sid, t, state = _session(42)
    target_ids = [a for a in t["unread_target_announcement_ids"].split(",") if a]
    for aid in target_ids[:-1]:  # leave one unread
        mark_announcement_read(
            aid, SessionScopedRequest(session_id=sid), session_manager=sm
        )
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is False


def test_branch1_mark_all_read_fails_on_collateral():
    """Using 'mark all as read' touches unread announcements in OTHER courses,
    violating the critical collateral invariant/constraint."""
    sm, sid, t, state = _session(42)
    mark_all_announcements_read(
        SessionScopedRequest(session_id=sid), session_manager=sm
    )
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is False


def test_branch1_wrong_branch_action_fails():
    """On a Branch-1 seed, taking the Branch-2 action (submitting the appeal)
    must fail — the discriminator genuinely gates the branch."""
    sm, sid, t, state = _session(42)
    exam = t["exam_assignment_id"]
    a = state.get_assignment(exam)
    # seed 42 midterm is graded with attempts exhausted; even if a submit were
    # possible it is the wrong branch. Guard against the endpoint raising.
    try:
        submit_assignment(
            exam,
            SubmitAssignmentRequest(session_id=sid, file_name="curve_appeal.pdf"),
            session_manager=sm,
        )
    except Exception:
        pass
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is False


# ── Branch 2: curve does not change the letter grade (seed 2) ────────────────

def test_branch2_correct_submits_appeal_passes():
    """Submitting curve_appeal.pdf to the midterm via the real endpoint passes
    with full score on a discriminator='false' seed."""
    sm, sid, t, state = _session(2)
    assert t["curve_changes_letter"] == "false", "seed 2 must select Branch 2"
    exam = t["exam_assignment_id"]
    submit_assignment(
        exam,
        SubmitAssignmentRequest(session_id=sid, file_name="curve_appeal.pdf"),
        session_manager=sm,
    )
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99


def test_branch2_wrong_file_name_fails():
    """Submitting the wrong file name fails the exact-literal predicate."""
    sm, sid, t, state = _session(2)
    exam = t["exam_assignment_id"]
    submit_assignment(
        exam,
        SubmitAssignmentRequest(session_id=sid, file_name="wrong_file.pdf"),
        session_manager=sm,
    )
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is False


def test_branch2_wrong_branch_action_fails():
    """On a Branch-2 seed, taking the Branch-1 action (marking announcements)
    must fail — the discriminator genuinely gates the branch."""
    sm, sid, t, state = _session(2)
    for aid in (a for a in t["unread_target_announcement_ids"].split(",") if a):
        mark_announcement_read(
            aid, SessionScopedRequest(session_id=sid), session_manager=sm
        )
    result = evaluate(
        task=get_task(TASK_ID), server_state=state, targets=t, trajectory=[]
    )
    assert result.get("success") is False
