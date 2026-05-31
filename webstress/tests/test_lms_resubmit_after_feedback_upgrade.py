"""Solvability proof for the upgraded lms_resubmit_after_feedback task.

The upgraded task requires the agent to:
  * find EVERY assignment whose status is "resubmit_requested" across all
    courses (multiple, scattered across a 4-course catalog),
  * resubmit each one through the real /resubmit endpoint,
  * name each revision file "revision_<weight_category>_v2.pdf" where the
    <weight_category> must be re-derived per-assignment from that assignment's
    own weight category.

The correct solution is driven through the REAL backend resubmit endpoint via
starlette TestClient, confirming achievability past the resubmit gate
(status == resubmit_requested, attempt_count < max_attempts). A near-miss
trajectory (uniform filename instead of the per-category computed name) is
asserted to fail.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from webstress.app import app
from webstress.runner import ensure_controller_secret
from webstress.backend.security import CONTROLLER_SECRET_HEADER
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_resubmit_after_feedback"
SEEDS = [42, 1, 7, 100, 2024, 13, 77]


@pytest.fixture()
def client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {CONTROLLER_SECRET_HEADER: app.state.controller_secret}


def _create(client: TestClient, seed: int) -> tuple[str, dict]:
    resp = client.post(
        "/api/env/lms/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=_headers(),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    return data["session_id"], data["resolved_targets"]


def _state(session_id: str):
    return app.state.session_manager.get(session_id)


def _flagged_ids(targets: dict) -> list[str]:
    raw = targets.get("resubmit_assignment_ids", "")
    return [x for x in raw.split(",") if x]


def _expected_file(state, assignment_id: str) -> str:
    a = state.get_assignment(assignment_id)
    return f"revision_{a.weight_category}_v2.pdf"


@pytest.mark.parametrize("seed", SEEDS)
def test_correct_trajectory_passes(client: TestClient, seed: int) -> None:
    """Resubmitting every flagged assignment with the per-category file passes."""
    sid, targets = _create(client, seed)
    ids = _flagged_ids(targets)
    assert len(ids) >= 2, f"expected >=2 flagged assignments, got {ids}"

    # Read the seeded (pre-action) weight categories before mutating.
    pre_state = _state(sid)
    expected_files = {aid: _expected_file(pre_state, aid) for aid in ids}

    for aid in ids:
        resp = client.post(
            f"/api/env/lms/assignments/{aid}/resubmit",
            json={"session_id": sid, "file_name": expected_files[aid]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()["assignment"]
        assert body["submission_status"] in ("submitted", "late")
        assert body["file_name"] == expected_files[aid]
        assert body["attempt_count"] >= 2

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"seed={seed} result={result}"
    assert result.get("score", 0.0) >= 0.99, f"seed={seed} result={result}"
    # The upgraded diff is richer than a single positive op.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_uniform_filename_fails(client: TestClient) -> None:
    """Resubmitting all flagged with a uniform (non-category) name fails."""
    sid, targets = _create(client, 42)
    ids = _flagged_ids(targets)
    assert len(ids) >= 2

    for aid in ids:
        resp = client.post(
            f"/api/env/lms/assignments/{aid}/resubmit",
            json={"session_id": sid, "file_name": "revision_v2.pdf"},
        )
        assert resp.status_code == 200, resp.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unexpected pass: {result}"


def test_missing_one_resubmission_fails(client: TestClient) -> None:
    """Leaving one flagged assignment un-resubmitted fails the bijection."""
    sid, targets = _create(client, 42)
    ids = _flagged_ids(targets)
    assert len(ids) >= 2

    pre_state = _state(sid)
    # Resubmit all but the last one, each with the correct per-category name.
    for aid in ids[:-1]:
        resp = client.post(
            f"/api/env/lms/assignments/{aid}/resubmit",
            json={"session_id": sid, "file_name": _expected_file(pre_state, aid)},
        )
        assert resp.status_code == 200, resp.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unexpected pass: {result}"


def test_extra_side_effect_message_fails(client: TestClient) -> None:
    """Correct resubmissions + an extra sent message trips the message guard."""
    sid, targets = _create(client, 42)
    ids = _flagged_ids(targets)
    pre_state = _state(sid)
    for aid in ids:
        resp = client.post(
            f"/api/env/lms/assignments/{aid}/resubmit",
            json={"session_id": sid, "file_name": _expected_file(pre_state, aid)},
        )
        assert resp.status_code == 200, resp.text

    # Side effect the instruction explicitly forbids.
    msg = client.post(
        "/api/env/lms/messages/send",
        json={"session_id": sid, "to": "advisor", "subject": "hi", "body": "done"},
    )
    assert msg.status_code == 200, msg.text

    state = _state(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"unexpected pass: {result}"
