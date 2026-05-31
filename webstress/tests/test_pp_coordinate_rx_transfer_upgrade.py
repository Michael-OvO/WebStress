"""Solvability + difficulty proof for the hardened pp_coordinate_rx_transfer.

The task was escalated in-place (still `hard`) by adding a *re-derivable*
discriminator: the destination retail pharmacy is no longer "the first
non-default retail pharmacy" — it is the non-default, non-mail-order retail
pharmacy with the LOWEST dispensing fee (id tie-break). The agent must compare
fees across 3 retail pharmacies, while a mail-order pharmacy (often the lowest
fee overall) is an explicit trap, and the bijection now saturates over 6 active
prescriptions.

The correct solution is driven through the REAL backend endpoints
(POST /medications/{id}/transfer and POST /profile/pharmacy/{id}/set-default)
to confirm it is achievable past server guards. The session is created on the
TestClient app's own SessionManager so HTTP calls and the evaluator observe the
same state.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task

_PREFIX = "/api/env/patient_portal"


def _make_session(seed: int = 42):
    """Create a session on the TestClient app's SessionManager.

    Returns (client, sid, targets, state). Driving the real endpoints through
    `client` mutates exactly the `state` the evaluator will read.
    """
    client = TestClient(app)
    sm = client.app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal",
        task_id="pp_coordinate_rx_transfer",
        seed=seed,
    )
    return client, sid, dict(targets), sm.get_state(sid)


def _evaluate(state, targets):
    return evaluate(
        task=get_task("pp_coordinate_rx_transfer"),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )


def test_correct_trajectory_via_real_endpoints_passes():
    """Transfer all active rxes to the cheapest retail pharmacy + set default."""
    client, sid, targets, state = _make_session()
    dest = targets["cheapest_retail_pharmacy_id"]

    # Sanity: the discriminator is meaningful — destination is a real,
    # non-default, non-mail-order retail pharmacy.
    pmap = {p.id: p for p in state.pharmacies}
    assert dest in pmap
    assert pmap[dest].is_mail_order is False
    assert dest != targets["default_pharmacy_id"]
    assert dest != targets["mail_order_pharmacy_id"]

    # 1. Transfer every active prescription via the real transfer endpoint.
    for rx_id in targets["active_rx_ids"]:
        resp = client.post(
            f"{_PREFIX}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": dest},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["pharmacy_id"] == dest

    # 2. Set the cheapest retail pharmacy as the new default via the real endpoint.
    resp = client.post(
        f"{_PREFIX}/profile/pharmacy/{dest}/set-default",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["is_default"] is True

    result = _evaluate(state, targets)
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # canonical_diff is richer than a 2-check eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_naive_first_retail_destination_fails():
    """Picking the *first* non-default retail pharmacy (the old trivial answer)
    instead of the cheapest one fails, because for seed 42 they differ."""
    client, sid, targets, state = _make_session()
    naive = targets["new_default_pharmacy_id"]
    cheapest = targets["cheapest_retail_pharmacy_id"]
    # Guard the premise: seed 42 is one where the two genuinely differ.
    assert naive != cheapest, "seed 42 must distinguish naive vs cheapest"

    for rx_id in targets["active_rx_ids"]:
        resp = client.post(
            f"{_PREFIX}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": naive},
        )
        assert resp.status_code == 200, resp.text
    resp = client.post(
        f"{_PREFIX}/profile/pharmacy/{naive}/set-default",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"result: {result}"


def test_mail_order_destination_trap_fails():
    """Transferring to the mail-order pharmacy (often the lowest fee overall,
    but explicitly forbidden) fails."""
    client, sid, targets, state = _make_session()
    mail = targets["mail_order_pharmacy_id"]

    for rx_id in targets["active_rx_ids"]:
        resp = client.post(
            f"{_PREFIX}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": mail},
        )
        assert resp.status_code == 200, resp.text
    resp = client.post(
        f"{_PREFIX}/profile/pharmacy/{mail}/set-default",
        json={"session_id": sid},
    )
    assert resp.status_code == 200, resp.text

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"result: {result}"


def test_partial_transfer_leaves_default_unchanged_fails():
    """Transferring only some active rxes (and not setting the new default)
    fails the saturating bijection + default-flip obligations."""
    client, sid, targets, state = _make_session()
    dest = targets["cheapest_retail_pharmacy_id"]

    # Move only the first three active rxes; leave the rest + default alone.
    for rx_id in targets["active_rx_ids"][:3]:
        resp = client.post(
            f"{_PREFIX}/medications/{rx_id}/transfer",
            json={"session_id": sid, "pharmacy_id": dest},
        )
        assert resp.status_code == 200, resp.text

    result = _evaluate(state, targets)
    assert result.get("success") is False, f"result: {result}"
