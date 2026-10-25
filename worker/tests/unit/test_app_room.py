from copy import deepcopy

import pytest

from tandem_worker.contracts import ContractError
from tandem_worker.live import validate_app_room


def context():
    return {"constraints": [{"member_id": 1, "key": "flight_budget_total"}], "room": {"settings": {"components": ["FLIGHT"]}}, "members": [
        {"member_id": 1, "display_name": "p1", "active": True, "participation_preferences": {"confirmed": True}}]}


def test_app_room_matches_members_not_config_private_constraints(live_case):
    validate_app_room(live_case.config, context())


@pytest.mark.parametrize("change,error", [
    ("duplicate", "ROOM_DISPLAY_NAMES_NOT_UNIQUE"),
    ("missing", "ROOM_CONFIG_MEMBER_MISMATCH"),
    ("component", "ROOM_CONFIG_COMPONENT_MISMATCH"),
    ("preferences", "ROOM_PARTICIPATION_NOT_CONFIRMED"),
])
def test_app_room_rejects_mismatch_before_providers(live_case, change, error):
    ctx = context()
    if change == "duplicate":
        ctx["members"].append(deepcopy(ctx["members"][0]))
    elif change == "missing":
        ctx["members"][0]["display_name"] = "other"
    elif change == "component":
        ctx["room"]["settings"]["components"].append("STAY")
    else:
        ctx["members"][0]["participation_preferences"]["confirmed"] = False
    with pytest.raises(ContractError, match=error):
        validate_app_room(live_case.config, ctx)
    assert not live_case.apify.starts and not live_case.claude.requests


def test_room_binding_cannot_repurpose_provider_state(tmp_path):
    from tandem_worker.live import bind_room

    bind_room(tmp_path, 1)
    bind_room(tmp_path, 1)
    with pytest.raises(ContractError, match="LIVE_STATE_ROOM_MISMATCH"):
        bind_room(tmp_path, 2)


def test_automatic_worker_requires_each_travellers_confirmed_flight_input(live_case):
    ctx = context()
    ctx['constraints'] = []
    with pytest.raises(ContractError, match='ROOM_CONSTRAINTS_NOT_CONFIRMED'):
        validate_app_room(live_case.config, ctx)
