"""Solvability proof for the upgraded lms_module_quiz_unlock task.

The upgraded task escalates from a single-module-complete-plus-cascade task
to an EXPERT ordered multi-module chain:

  * Modules 2, 3, 4, 5 must be completed IN ORDER (a saturating bijection over
    the seeded ``modules_to_complete`` target list), and every one of the 4
    content items in each module must be completed first.
  * Completing Module 5 cascades Module 6 from locked -> available (the
    capstone unlock), and Module 6 must NOT itself be completed.
  * Module 7 (the terminal locked module) must remain locked, guarded by a
    dedicated CRITICAL invariant + constraint, so a greedy agent that keeps
    completing modules fails.

This drives the CORRECT solution through the REAL LMS backend endpoints via
starlette TestClient (item-complete + module-complete), then evaluates with the
unified evaluator. A near-miss (over-completing Module 6) is asserted to fail.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.routes.lms import SessionCreateRequest, create_session
from webstress.injector.middleware import clear_all_degradations
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


TASK_ID = "lms_module_quiz_unlock"


@pytest.fixture(autouse=True)
def _reset_degradations():
    clear_all_degradations()
    yield
    clear_all_degradations()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _open_session() -> tuple[str, dict]:
    """Create a real app-managed session and return (session_id, targets)."""
    payload = create_session(
        SessionCreateRequest(task_id=TASK_ID, seed=42),
        session_manager=app.state.session_manager,
    )
    sid = payload["session_id"]
    state = app.state.session_manager.get(sid)
    return sid, dict(state.resolved_targets)


def _complete_module_via_api(client: TestClient, sid: str, module_id: str) -> None:
    """Complete every content item then mark the module done, via real routes."""
    state = app.state.session_manager.get(sid)
    module = state.get_module(module_id)
    assert module is not None, f"module {module_id} missing"
    for idx in range(len(module.content_items)):
        resp = client.post(
            f"/api/env/lms/modules/{module_id}/items/{idx}/complete",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/env/lms/modules/{module_id}/complete",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text


def test_correct_ordered_chain_completion_passes(client: TestClient):
    """Completing Modules 2-5 in order via the real backend passes evaluation."""
    sid, targets = _open_session()
    try:
        to_complete = targets["modules_to_complete"].split(",")
        assert len(to_complete) == 4, to_complete

        # Drive the ordered chain through the REAL endpoints. Each module is
        # only unlockable once its predecessor is completed (server gate), so
        # the order matters — out-of-order calls would 422.
        for module_id in to_complete:
            _complete_module_via_api(client, sid, module_id)

        state = app.state.session_manager.get(sid)

        # Sanity: the cascade left Module 6 available and Module 7 locked.
        final_unlocked = state.get_module(targets["final_unlocked_module_id"])
        terminal_locked = state.get_module(targets["terminal_locked_module_id"])
        assert final_unlocked.status == "available", final_unlocked.status
        assert terminal_locked.status == "locked", terminal_locked.status

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is True, f"result: {result}"
        assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
        # Richer than the legacy single-update check.
        assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 3
    finally:
        app.state.session_manager.destroy(sid)


def test_over_completing_capstone_module_fails(client: TestClient):
    """Completing Module 6 as well (greedy over-completion) fails evaluation.

    Module 6 must stay merely AVAILABLE after the cascade. If the agent also
    finishes Module 6's items and marks it done, update[1] (status==available)
    no longer holds and the CRITICAL 'Module 6 unlocked but not completed'
    constraint flips — the task must reject this near-miss.
    """
    sid, targets = _open_session()
    try:
        # Complete the four intended modules...
        for module_id in targets["modules_to_complete"].split(","):
            _complete_module_via_api(client, sid, module_id)
        # ...then ALSO over-complete Module 6 (now available via cascade).
        _complete_module_via_api(client, sid, targets["final_unlocked_module_id"])

        state = app.state.session_manager.get(sid)
        assert state.get_module(targets["final_unlocked_module_id"]).status == "completed"

        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"expected failure, got {result}"
    finally:
        app.state.session_manager.destroy(sid)


def test_partial_chain_completion_fails(client: TestClient):
    """Completing only Modules 2 and 3 (missing 4 and 5) fails the bijection."""
    sid, targets = _open_session()
    try:
        partial = targets["modules_to_complete"].split(",")[:2]
        for module_id in partial:
            _complete_module_via_api(client, sid, module_id)

        state = app.state.session_manager.get(sid)
        result = evaluate(
            task=get_task(TASK_ID),
            server_state=state,
            targets=dict(targets),
            trajectory=[],
        )
        assert result.get("success") is False, f"expected failure, got {result}"
    finally:
        app.state.session_manager.destroy(sid)
