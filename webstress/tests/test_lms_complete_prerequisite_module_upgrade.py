"""Solvability proof for the upgraded lms_complete_prerequisite_module task.

The upgraded task (re-tiered medium -> hard) requires the agent to advance a
``mixed`` prerequisite chain by completing EXACTLY two consecutive modules:

  * module index 1 (``next_available_module_id``) — the next available module, and
  * module index 2 (``first_locked_module_id``) — a min-score-gated successor that
    becomes unlocked once the earlier module is completed (its gate carries no
    linked assignment, so completion alone satisfies the min-score threshold).

Completing the second module triggers the server cascade
(``_unlock_available_modules``) which flips module index 3 from ``locked`` to
``available``. Module index 0 (already completed) and index 4 (still
min-score-locked) must remain frozen, and no other module may be completed.

The correct solution is driven through the REAL backend endpoints
(``POST /api/env/lms/modules/{id}/items/{idx}/complete`` and
``POST /api/env/lms/modules/{id}/complete``) so the test confirms the seed is
achievable past every server gate (unlock + all-content-items guards + cascade).
The session is created on ``app.state.session_manager`` — the exact same manager
the API routes resolve via ``get_session_manager`` — so the TestClient operates on
the very state object we then hand to ``evaluate``.
"""

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_complete_prerequisite_module"
PREFIX = "/api/env/lms"


def _new_session():
    """Create a session on the SAME manager the API routes use."""
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session("lms", TASK_ID, 42)
    return sm, sid, dict(targets)


def _module_ids(targets):
    return targets["module_ids"].split(",")


def _complete_module_via_backend(client: TestClient, sid: str, module_id: str, state) -> None:
    """Complete every content item, then complete the module, via real endpoints."""
    module = state.get_module(module_id)
    assert module is not None, f"module {module_id} missing"
    for idx in range(len(module.content_items)):
        resp = client.post(
            f"{PREFIX}/modules/{module_id}/items/{idx}/complete",
            json={"session_id": sid},
        )
        assert resp.status_code == 200, (module_id, idx, resp.status_code, resp.text)
    resp = client.post(
        f"{PREFIX}/modules/{module_id}/complete",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, (module_id, resp.status_code, resp.text)


def test_correct_trajectory_evaluates_to_pass():
    """Driving the two-module chain through the real backend passes evaluation."""
    sm, sid, targets = _new_session()
    state = sm.get(sid)
    mids = _module_ids(targets)
    to_complete = mids[1:3]  # module index 1 and index 2

    # Sanity: the seed matches the design the canonical_diff relies on.
    assert targets["next_available_module_id"] == mids[1]
    assert targets["first_locked_module_id"] == mids[2]
    m_avail = state.get_module(mids[1])
    m_gate = state.get_module(mids[2])
    assert m_avail.status == "available"
    assert m_gate.status == "locked" and m_gate.unlock_condition == "min_score"

    client = TestClient(app)
    # Complete in sequence order so the second module's gate is satisfied first.
    for module_id in to_complete:
        _complete_module_via_backend(client, sid, module_id, sm.get(sid))

    state = sm.get(sid)
    by_id = {m.id: m for m in state.modules}
    assert by_id[mids[1]].status == "completed"
    assert by_id[mids[2]].status == "completed"
    assert by_id[mids[3]].status == "available"  # cascade
    assert by_id[mids[4]].status == "locked"      # stays min-score-locked
    assert by_id[mids[0]].status == "completed"   # pre-existing
    assert len([m for m in state.modules if m.status == "completed"]) == 3

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_only_first_module_evaluates_to_fail():
    """Completing only the next available module (skipping the gated successor) fails."""
    sm, sid, targets = _new_session()
    mids = _module_ids(targets)

    client = TestClient(app)
    _complete_module_via_backend(client, sid, mids[1], sm.get(sid))

    state = sm.get(sid)
    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected fail, got: {result}"


def test_wrong_overshoot_module_evaluates_to_fail():
    """Completing a third module (overshooting the named pair) trips the guards."""
    sm, sid, targets = _new_session()
    mids = _module_ids(targets)

    client = TestClient(app)
    # Complete index 1, 2, AND 3 — one too many; cascade then unlocks index 4 too.
    for module_id in mids[1:4]:
        _complete_module_via_backend(client, sid, module_id, sm.get(sid))

    state = sm.get(sid)
    completed = [m.id for m in state.modules if m.status == "completed"]
    assert mids[3] in completed  # we really did overshoot

    task = get_task(TASK_ID)
    result = evaluate(task=task, server_state=state, targets=dict(targets), trajectory=[])
    assert result.get("success") is False, f"expected fail, got: {result}"
