"""Solvability proof for the upgraded ``lms_read_urgent_announcement`` task.

The task was re-tiered easy -> medium. The single graded action is no longer
"find the urgent announcement" — there are now FOUR urgent unread announcements
competing, and the agent must mark exactly the *most recently posted* urgent
unread one (``target_urgent_announcement_id``), derived in the seed builder via
top-1-by-recency over distinct timestamps. Marking the trivial first urgent
(``urgent_announcement_id``) is a near-miss that must FAIL.

Correct path is driven through the REAL backend endpoint
``POST /api/env/lms/announcements/{id}/read`` via TestClient, proving the action
is achievable past all gates; evaluation goes through the real ``evaluate()``.
"""

from starlette.testclient import TestClient

from webstress.app import app
from webstress.backend.state import SessionManager, materialize_task_state
from webstress.runner import controller_headers, ensure_controller_secret
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

TASK_ID = "lms_read_urgent_announcement"
ENV_ID = "lms"
SEED = 42


def _client() -> TestClient:
    app.state.controller_secret = ensure_controller_secret()
    return TestClient(app)


def _create_api_session(client: TestClient, seed: int = SEED) -> tuple[str, dict]:
    r = client.post(
        f"/api/env/{ENV_ID}/session",
        json={"task_id": TASK_ID, "seed": seed},
        headers=controller_headers(),
    )
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    # Targets are deterministic for the seed; re-materialize to read them.
    _, _, targets, _ = materialize_task_state(ENV_ID, TASK_ID, seed)
    return sid, dict(targets)


def _mark_read(client: TestClient, sid: str, ann_id: str):
    return client.post(
        f"/api/env/{ENV_ID}/announcements/{ann_id}/read",
        json={"session_id": sid},
        headers={"Referer": f"http://testserver/env/{ENV_ID}/courses?session={sid}"},
    )


def test_targets_are_nontrivial():
    """The graded answer must NOT be the trivial first urgent announcement,
    and there must be several urgent unread announcements competing."""
    _, _, targets, _ = materialize_task_state(ENV_ID, TASK_ID, SEED)
    targets = dict(targets)
    answer = targets["target_urgent_announcement_id"]
    first_urgent = targets["urgent_announcement_id"]
    urgent_unread = targets["urgent_unread_announcement_ids"].split(",")
    assert answer, "no discriminator target produced"
    assert len(urgent_unread) >= 3, urgent_unread
    assert answer in urgent_unread
    # The discriminator genuinely matters: the answer is a later urgent than
    # the first one an agent encounters.
    assert answer != first_urgent, (answer, first_urgent)


def test_correct_trajectory_via_real_endpoint_passes():
    """Marking the most-recent urgent unread announcement read passes."""
    client = _client()
    sid, targets = _create_api_session(client)
    answer = targets["target_urgent_announcement_id"]

    resp = _mark_read(client, sid, answer)
    assert resp.status_code == 200, resp.text

    state = app.state.session_manager.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"score: {result.get('score')}"
    # canonical_diff is richer than a 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_wrong_first_urgent_announcement_fails():
    """Marking the trivial first urgent (NOT the most recent) fails: it both
    misses the positive obligation and trips the non-target invariant."""
    client = _client()
    sid, targets = _create_api_session(client)
    wrong = targets["urgent_announcement_id"]
    assert wrong != targets["target_urgent_announcement_id"]

    resp = _mark_read(client, sid, wrong)
    assert resp.status_code == 200, resp.text

    state = app.state.session_manager.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_mark_all_read_fails():
    """Bulk mark-all-read marks every announcement read, which violates the
    'other urgent unread left unread' constraint and the non-target invariant."""
    client = _client()
    sid, targets = _create_api_session(client)

    resp = client.post(
        f"/api/env/{ENV_ID}/announcements/mark_all_read",
        json={"session_id": sid},
        headers={"Referer": f"http://testserver/env/{ENV_ID}/courses?session={sid}"},
    )
    assert resp.status_code == 200, resp.text

    state = app.state.session_manager.get(sid)
    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"


def test_extra_side_effect_message_fails():
    """Doing the right read PLUS sending a message trips the no-messages
    constraint/invariant."""
    sm = SessionManager()
    sid, targets, _ = sm.create_session(env_id=ENV_ID, task_id=TASK_ID, seed=SEED)
    targets = dict(targets)
    state = sm.get_state(sid)

    # Correct read (mirrors POST /announcements/{id}/read).
    ann = state.get_announcement(targets["target_urgent_announcement_id"])
    assert ann is not None
    ann.is_read = True
    # Illegal side-effect: send a message.
    state.sent_messages.append(
        {"to": "advisor", "subject": "fyi", "body": "done", "sent_at": "x", "from": "s"}
    )

    result = evaluate(
        task=get_task(TASK_ID),
        server_state=state,
        targets=targets,
        trajectory=[],
    )
    assert result.get("success") is False, f"result: {result}"
