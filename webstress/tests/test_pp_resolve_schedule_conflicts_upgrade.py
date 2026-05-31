"""Solvability proof for the hardened pp_resolve_schedule_conflicts task.

The task seeds several groups of double/triple-booked appointments that share
an exact date+time. In each group exactly one appointment was booked first
(earliest ``booked_at``) and must be KEPT; every other (later-booked) member of
the group must be CANCELLED. Non-conflicting upcoming appointments must be left
untouched.

The CORRECT solution is driven through the REAL backend cancel endpoint
(POST /api/env/patient_portal/appointments/{id}/cancel) using the seed's
precomputed ``cluster_cancel_apt_ids`` as the intended answer — this confirms
the answer set is achievable past the route's status-gate. A WRONG trajectory
(cancelling the earliest-booked keeper instead) is asserted to fail.
"""

from collections import defaultdict

from starlette.testclient import TestClient

from webstress.app import app
from webstress.tasks._evaluator import evaluate
from webstress.tasks._registry import get_task


_TASK_ID = "pp_resolve_schedule_conflicts"
_CANCEL_URL = "/api/env/patient_portal/appointments/{aid}/cancel"


def _seed_session():
    """Create a seeded session on the app's SessionManager.

    Returns ``(client, sid, targets, state)``. The state is the live object the
    routes mutate, so post-cancel it reflects the real backend mutations.
    """
    client = TestClient(app)
    sm = app.state.session_manager
    sid, targets, _ = sm.create_session(
        env_id="patient_portal", task_id=_TASK_ID, seed=42
    )
    state = sm.get_state(sid)
    return client, sid, dict(targets), state


def test_seed_partition_is_a_valid_earliest_booked_grouping():
    """The precomputed keep/cancel sets match the group-by-datetime +
    earliest-booked tie-break the agent must re-derive, and the only
    same-datetime scheduled groups are the conflict clusters."""
    _client, _sid, targets, state = _seed_session()

    cancel = set(targets["cluster_cancel_apt_ids"])
    keep = set(targets["cluster_keep_apt_ids"])
    cluster_all = set(targets["cluster_all_apt_ids"])

    assert cancel, "expected a non-empty cancel set"
    assert keep, "expected a non-empty keep set"
    assert cancel.isdisjoint(keep)
    assert cancel | keep == cluster_all
    # Multi-group, multi-cancel scenario (genuinely harder than a single pair).
    assert len(keep) >= 3
    assert len(cancel) >= 4

    # Re-derive the partition from raw state and confirm it matches.
    by_dt: dict[str, list] = defaultdict(list)
    for a in state.appointments:
        if a.id in cluster_all:
            by_dt[a.datetime.isoformat()].append(a)
    derived_keep, derived_cancel = set(), set()
    for _dt, apts in by_dt.items():
        assert len(apts) >= 2, "every cluster group must be a real conflict"
        apts_sorted = sorted(apts, key=lambda a: a.booked_at)
        derived_keep.add(apts_sorted[0].id)
        derived_cancel.update(a.id for a in apts_sorted[1:])
    assert derived_keep == keep
    assert derived_cancel == cancel

    # No NON-cluster scheduled appointment may share a datetime with another
    # scheduled appointment (otherwise the instruction would imply cancelling
    # outside the precomputed set).
    all_sched: dict[str, list] = defaultdict(list)
    for a in state.appointments:
        if a.status == "scheduled":
            all_sched[a.datetime.isoformat()].append(a.id)
    for _dt, ids in all_sched.items():
        if len(ids) > 1:
            assert all(i in cluster_all for i in ids), (
                f"stray non-cluster conflict at {_dt}: {ids}"
            )


def test_correct_trajectory_via_real_endpoint_passes():
    """Cancelling exactly the later-booked duplicate of every group via the
    real cancel endpoint evaluates to success."""
    client, sid, targets, state = _seed_session()

    for aid in targets["cluster_cancel_apt_ids"]:
        resp = client.post(
            _CANCEL_URL.format(aid=aid),
            json={"session_id": sid, "reason": "Duplicate booking for the same slot"},
        )
        assert resp.status_code == 200, (aid, resp.status_code, resp.text)
        assert resp.json()["status"] == "cancelled"

    # Keepers and non-conflicting appointments must remain scheduled.
    for aid in targets["cluster_keep_apt_ids"]:
        assert state.get_appointment(aid).status == "scheduled"

    result = evaluate(
        task=get_task(_TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is True, f"result: {result}"
    assert result.get("score", 0.0) >= 0.99, f"result: {result}"
    # Richer than the legacy single-update eval.
    assert len(result.get("checks", [])) + len(result.get("negative_checks", [])) > 2


def test_cancelling_earliest_keeper_fails():
    """Near-miss: cancelling a group's earliest-booked keeper (instead of the
    later-booked duplicate) must fail — it both leaves a cancel slot unmet and
    trips the critical 'keeper remains scheduled' constraint."""
    client, sid, targets, state = _seed_session()

    cancel = list(targets["cluster_cancel_apt_ids"])
    keep = list(targets["cluster_keep_apt_ids"])

    # Cancel all-but-one of the correct duplicates, then cancel a KEEPER
    # instead of the remaining duplicate.
    for aid in cancel[:-1]:
        resp = client.post(
            _CANCEL_URL.format(aid=aid),
            json={"session_id": sid},
        )
        assert resp.status_code == 200
    wrong = keep[0]
    resp = client.post(_CANCEL_URL.format(aid=wrong), json={"session_id": sid})
    assert resp.status_code == 200
    assert state.get_appointment(wrong).status == "cancelled"

    result = evaluate(
        task=get_task(_TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"expected failure, got: {result}"


def test_cancelling_a_non_conflicting_appointment_fails():
    """Near-miss: cancelling a non-conflicting upcoming appointment in addition
    to the correct set must fail the invariant freezing all non-cancel rows."""
    client, sid, targets, state = _seed_session()

    cancel_set = set(targets["cluster_cancel_apt_ids"])
    cluster_all = set(targets["cluster_all_apt_ids"])

    # Do the correct cancels first.
    for aid in targets["cluster_cancel_apt_ids"]:
        resp = client.post(_CANCEL_URL.format(aid=aid), json={"session_id": sid})
        assert resp.status_code == 200

    # Then cancel one extra scheduled appointment that is NOT a conflict member.
    extra = next(
        a for a in state.appointments
        if a.status == "scheduled" and a.id not in cluster_all
    )
    resp = client.post(_CANCEL_URL.format(aid=extra.id), json={"session_id": sid})
    assert resp.status_code == 200

    result = evaluate(
        task=get_task(_TASK_ID),
        server_state=state,
        targets=dict(targets),
        trajectory=[],
    )
    assert result.get("success") is False, f"expected failure, got: {result}"
