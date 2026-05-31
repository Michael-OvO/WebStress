"""Solvability proof for the upgraded (expert-tier) lms_resubmit_flagged task.

Confirms:
- The seed produces 4 flagged (resubmit_requested) assignments, each with a
  distinct prior attempt_count, all resubmittable past the real backend gates.
- Driving the CORRECT solution through the REAL resubmit endpoint
  (POST /assignments/{id}/resubmit handler) with the per-assignment derived
  filename "revision_v{attempt_count+1}.pdf" evaluates to success >= 0.99.
- A near-miss (uniform filename / one assignment skipped) fails.
"""
from __future__ import annotations

from webstress.backend.routes.lms import (
    ResubmitAssignmentRequest,
    resubmit_assignment,
)
from webstress.backend.state import SessionManager
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_resubmit_flagged"


def _new_session():
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id="lms", task_id=TASK_ID, seed=42)
    state = sm.get_state(sid)
    return sm, sid, dict(targets), state


def _expected_filename(state, assignment_id: str) -> str:
    """Per-row filename: revision_v{current attempt_count + 1}.pdf."""
    a = state.get_assignment(assignment_id)
    return f"revision_v{a.attempt_count + 1}.pdf"


def test_seed_is_well_formed():
    """4 distinct flagged assignments, all resubmittable, varied attempt counts."""
    sm, sid, targets, state = _new_session()
    try:
        rs = targets["resubmit_assignment_ids"].split(",")
        assert len(rs) == 4, f"expected 4 flagged assignments, got {rs}"
        attempts = set()
        for aid in rs:
            a = state.get_assignment(aid)
            assert a is not None
            assert a.submission_status == "resubmit_requested"
            assert a.attempt_count < a.max_attempts, "must have a remaining attempt"
            assert a.feedback, "flagged assignment must carry feedback to read"
            attempts.add(a.attempt_count)
        # Varied prior attempt counts force per-row filename re-derivation.
        assert len(attempts) >= 2, f"attempt counts not varied: {attempts}"
        # resubmit_filenames target agrees with per-row derivation.
        fns = dict(p.split(":") for p in targets["resubmit_filenames"].split(","))
        for aid in rs:
            assert fns[aid] == _expected_filename(state, aid)
    finally:
        sm.destroy(sid)


def test_correct_trajectory_via_real_endpoint_passes():
    """Resubmitting every flagged assignment via the real handler passes."""
    sm, sid, targets, state = _new_session()
    try:
        rs = targets["resubmit_assignment_ids"].split(",")
        for aid in rs:
            fname = _expected_filename(state, aid)
            resubmit_assignment(
                assignment_id=aid,
                body=ResubmitAssignmentRequest(session_id=sid, file_name=fname),
                session_manager=sm,
            )

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"score too low: {result}"
        # Richer than a 2-check eval (bijection + invariants + constraint).
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2
    finally:
        sm.destroy(sid)


def test_uniform_filename_near_miss_fails():
    """Using one literal filename for all (ignoring per-row attempt) fails."""
    sm, sid, targets, state = _new_session()
    try:
        rs = targets["resubmit_assignment_ids"].split(",")
        for aid in rs:
            # Naive agent: reuses "revision_v2.pdf" for every assignment,
            # which is wrong for any assignment whose prior attempt_count != 1.
            resubmit_assignment(
                assignment_id=aid,
                body=ResubmitAssignmentRequest(session_id=sid, file_name="revision_v2.pdf"),
                session_manager=sm,
            )

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"should fail, got: {result}"
    finally:
        sm.destroy(sid)


def test_skipping_one_flagged_assignment_fails():
    """Missing one flagged assignment (incomplete saturation) fails."""
    sm, sid, targets, state = _new_session()
    try:
        rs = targets["resubmit_assignment_ids"].split(",")
        for aid in rs[:-1]:  # skip the last flagged assignment
            fname = _expected_filename(state, aid)
            resubmit_assignment(
                assignment_id=aid,
                body=ResubmitAssignmentRequest(session_id=sid, file_name=fname),
                session_manager=sm,
            )

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"should fail, got: {result}"
    finally:
        sm.destroy(sid)
