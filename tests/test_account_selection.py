import asyncio
from copy import deepcopy
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nonebot.exception import FinishedException
from tortoise import Tortoise


@pytest.fixture
def selection(mystool):
    return import_module(f"{mystool.__name__}.command.account_selection")


@pytest.fixture
def accounts(mystool):
    model = import_module(f"{mystool.__name__}.model.data")
    return {
        uid: model.UserAccount(
            cookies=model.BBSCookies(stuid=uid),
            device_id_ios=f"ios-{uid}", device_id_android=f"android-{uid}",
            community_nickname="星夜",
        )
        for uid in ("73736863", "260806507")
    }


def test_numbered_menu_hides_uid_and_distinguishes_duplicate_names(selection, accounts):
    menu = selection.AccountMenu("qq-owner", "owner-version", accounts)
    lines = str(menu.prompt("请选择续登账号", allow_new=True)).splitlines()
    assert lines[1].startswith("1. 星夜（账号 ")
    assert lines[2].startswith("2. 星夜（账号 ")
    assert lines[1][3:] != lines[2][3:]
    assert lines[3] == "0. 新增账号"
    assert all(uid not in "\n".join(lines) for uid in accounts)
    assert menu.pick("1", selection.AccountMenu("qq-owner", "owner-version", accounts)) == sorted(accounts)[0]


@pytest.mark.parametrize("change", ["deleted", "rebound", "recreated"])
def test_stale_number_can_never_select_a_different_account(selection, accounts, change):
    menu = selection.AccountMenu("qq-owner", "owner-version", accounts)
    current = dict(accounts)
    owner = "qq-owner"
    selected = sorted(accounts)[0]
    if change == "deleted":
        current.pop(selected)
    if change == "rebound":
        owner = "different-owner"
    if change == "recreated":
        current[selected] = accounts[selected].copy(update={"device_id_android": "different-device"})
    with pytest.raises(selection.StaleAccountChoice):
        menu.pick("1", selection.AccountMenu(owner, "owner-version", current))


def test_existing_account_menu_backfills_names_without_a_conflicting_save(mystool, selection, accounts, monkeypatch):
    package = mystool.__name__
    model = import_module(f"{package}.model.data")
    codec = import_module(f"{package}.storage.codec")
    session_module = import_module(f"{package}.storage.session")
    profiles = import_module(f"{package}.services.account_profiles")
    repository = session_module.Repository
    legacy = {uid: account.copy(update={"community_nickname": None}) for uid, account in accounts.items()}
    records = codec.encode(model.PluginData(users={"owner": model.UserData(accounts=legacy)}))
    for row in records["accounts"].values():
        row.pop("community_nickname", None)

    async def lookup(uid, _device, _cookies):
        return {"73736863": "星夜", "260806507": "提瓦特旅人"}[uid]

    monkeypatch.setattr(profiles, "fetch_community_nickname", lookup)
    matcher = SimpleNamespace(send=AsyncMock())
    event = SimpleNamespace(get_user_id=lambda: "owner")
    state = {}

    async def scenario():
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": [f"{package}.storage.models"]})
        try:
            await Tortoise.generate_schemas()
            await session_module.initialize_storage()
            await repository.commit(await repository.load(), records)
            async with session_module.PluginDataManager.session() as session:
                await selection.ask_for_account(matcher, state, event, "请选择账号")
                await session.save()
            reply = str(matcher.send.await_args.args[0])
            assert "星夜" in reply and "提瓦特旅人" in reply
            assert all(uid not in reply for uid in legacy)
            saved = (await repository.load())["accounts"]
            assert saved[("owner", "73736863")]["community_nickname"] == "星夜"
            assert saved[("owner", "260806507")]["community_nickname"] == "提瓦特旅人"
        finally:
            await Tortoise.close_connections()

    asyncio.run(scenario())


@pytest.mark.parametrize("delete", [False, True])
def test_profile_backfill_does_not_conflict_with_account_changes(mystool, accounts, delete):
    package = mystool.__name__
    model = import_module(f"{package}.model.data")
    codec = import_module(f"{package}.storage.codec")
    repository = import_module(f"{package}.storage.repository").Repository
    uid = "73736863"
    key = ("owner", uid)
    base = codec.encode(model.PluginData(users={"owner": model.UserData(accounts={uid: accounts[uid]})}))
    base["accounts"][key].pop("community_nickname", None)
    latest = deepcopy(base)
    latest["accounts"][key]["community_nickname"] = "新的昵称"
    pending = deepcopy(base)
    if delete:
        del pending["accounts"][key]
    else:
        pending["accounts"][key]["enable_mission"] = False

    merged = repository.merge(base, pending, latest)
    if delete:
        assert key not in merged["accounts"]
    else:
        assert merged["accounts"][key]["community_nickname"] == "新的昵称"
        assert not merged["accounts"][key]["enable_mission"]


def test_committing_selected_account_checks_if_shared_access_was_revoked(mystool, selection, accounts):
    package = mystool.__name__
    model = import_module(f"{package}.model.data")
    codec = import_module(f"{package}.storage.codec")
    session_module = import_module(f"{package}.storage.session")
    repository = session_module.Repository
    uid = "73736863"
    records = codec.encode(model.PluginData(
        users={"owner": model.UserData(accounts={uid: accounts[uid]})},
        user_bind={"friend": "owner"},
    ))
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: "friend")

    async def scenario():
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": [f"{package}.storage.models"]})
        try:
            await Tortoise.generate_schemas()
            await session_module.initialize_storage()
            await repository.commit(await repository.load(), records)
            async with session_module.PluginDataManager.session() as session:
                state = {"account_menu": selection.AccountMenu.from_data(session.data, "friend"), "bbs_uid": uid}
                account = await selection.require_selected_account(matcher, state, event)
                account.enable_mission = False
                await repository.update(lambda current: current["bindings"].pop("friend"))
                with pytest.raises(FinishedException):
                    await selection.save_selected_account(matcher, state, event)
            assert (await repository.load())["accounts"][("owner", uid)]["enable_mission"]
            assert "绑定关系发生变化" in matcher.finish.await_args.args[0]
        finally:
            await Tortoise.close_connections()

    asyncio.run(scenario())
