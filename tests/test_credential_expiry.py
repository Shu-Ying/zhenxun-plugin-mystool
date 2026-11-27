import asyncio
from datetime import datetime, timezone
from importlib import import_module
import json
from types import SimpleNamespace

import httpx
import pytest
from tortoise import Tortoise

def make_account(mystool, uid="123"):
    models = import_module(f"{mystool.__name__}.model.data")
    return models.UserAccount(
        cookies=models.BBSCookies(stuid=uid, stoken_v2="v2_app", mid="mid", ltoken="web", cookie_token="cookie"),
        device_id_ios="ios",
        device_id_android="android",
    )


@pytest.fixture
def expiry_state(mystool, monkeypatch):
    credential_expiry = import_module(f"{mystool.__name__}.services.credential_expiry")

    account = make_account(mystool)
    records = {
        "users": {"owner": {"enable_notice": True}, "other": {"enable_notice": True}},
        "accounts": {
            ("owner", "123"): json.loads(account.json()),
            ("other", "123"): json.loads(account.json()),
        },
        "bindings": {"shared": "owner", "unrelated": "other"},
    }
    outgoing = []

    async def update(mutator):
        return mutator(records)

    async def send(user_id, message):
        outgoing.append((user_id, message))
        return True, None

    monkeypatch.setattr(credential_expiry.Repository, "update", update, raising=False)
    monkeypatch.setattr(credential_expiry, "send_private_msg", send)
    return credential_expiry, account, records, outgoing


def test_expiry_pauses_only_that_family_and_notifies_authorized_users_once(expiry_state, mystool):
    expiry_module, account, records, outgoing = expiry_state
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus(login_expired=True)

    async def check_expiry():
        first = expiry_module.CredentialExpiry("owner", account)
        second = expiry_module.CredentialExpiry("owner", account.copy(deep=True))
        await asyncio.gather(
            first.handle(status, "app", "米游币任务"),
            second.handle(status, "app", "实时便笺"),
        )
        fresh = type(account).parse_obj(records["accounts"][("owner", "123")])
        resolved = expiry_module.CredentialExpiry("owner", fresh)
        assert await resolved.skip("app", "米游币任务")
        assert not await resolved.skip("web", "游戏签到")
        unrelated = type(account).parse_obj(records["accounts"][("other", "123")])
        assert not unrelated.expired_credentials
        assert fresh.enable_mission and fresh.enable_game_sign and fresh.enable_resin

    asyncio.run(check_expiry())
    assert [user_id for user_id, _ in outgoing] == ["owner", "shared"]
    assert all("App" in message and "登录" in message for _, message in outgoing)


def test_old_request_cannot_pause_refreshed_credentials(expiry_state, mystool):
    expiry_module, account, records, outgoing = expiry_state
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus(login_expired=True)
    stale = expiry_module.CredentialExpiry("owner", account)
    current = records["accounts"][("owner", "123")]
    current["credential_versions"] = {"app": 1}

    async def check_stale_response():
        await stale.handle(status, "app", "米游币任务")
        refreshed = expiry_module.CredentialExpiry("owner", type(account).parse_obj(current))
        assert not await refreshed.skip("app", "米游币任务")

    asyncio.run(check_stale_response())
    assert not current.get("expired_credentials")
    assert not outgoing


def test_mixed_auth_error_explains_ambiguity_without_pausing(expiry_state, mystool):
    expiry_module, account, records, outgoing = expiry_state
    expiry = expiry_module.CredentialExpiry("owner", account)
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus(login_expired=True)

    async def check_mixed_response():
        for _ in range(2):
            assert await expiry.handle(status, "combined", "实时便笺")
        assert not await expiry.skip("app", "米游币任务")
        assert not await expiry.skip("web", "游戏签到")

    asyncio.run(check_mixed_response())
    assert [user_id for user_id, _ in outgoing] == ["owner", "shared"]
    assert all("无法确定" in message and "实时便笺" in message for _, message in outgoing)
    assert not records["accounts"][("owner", "123")].get("expired_credentials")


def test_manual_auth_tip_is_available_with_notifications_disabled(expiry_state, mystool):
    expiry_module, account, records, outgoing = expiry_state
    status_model = import_module(f"{mystool.__name__}.model.common").BaseApiStatus
    records["users"]["owner"]["enable_notice"] = False
    replies = []

    async def reply(message):
        replies.append(message)

    async def check_manual_response():
        expiry = expiry_module.CredentialExpiry("owner", account, reply=reply)
        assert not await expiry.handle(status_model(network_error=True), "web", "游戏签到")
        assert not await expiry.handle(status_model(need_verify=True), "web", "游戏签到")
        assert await expiry.handle(status_model(login_expired=True), "web", "游戏签到")

    asyncio.run(check_manual_response())
    assert len(replies) == 1
    assert "登录 Web』后按序号" in replies[0]
    assert "123" not in replies[0]
    assert not outgoing
    assert not records["users"]["owner"]["enable_notice"]


@pytest.mark.parametrize("note_api", ["genshin_note", "starrail_note"])
@pytest.mark.parametrize("flag", ["login_expired", "network_error"])
def test_note_preserves_game_record_failure_reason(mystool, monkeypatch, note_api, flag):
    api = import_module(f"{mystool.__name__}.api.common")

    async def failed_record(account):
        return api.BaseApiStatus(**{flag: True}), None

    monkeypatch.setattr(api, "get_game_record", failed_record)
    status, note = asyncio.run(getattr(api, note_api)(make_account(mystool)))
    assert note is None
    assert status.game_record_failed
    assert getattr(status, flag)


def test_genshin_widget_preserves_explicit_auth_error(mystool, monkeypatch):
    api = import_module(f"{mystool.__name__}.api.common")

    async def game_record(account):
        return api.BaseApiStatus(success=True), [SimpleNamespace(game_id=2, game_role_id="123", region="cn_gf01")]

    async def game_list():
        return api.BaseApiStatus(success=True), [SimpleNamespace(id=2, en_name="ys")]

    def respond(request):
        if request.url.path.endswith("/dailyNote"):
            return httpx.Response(200, json={"retcode": 1034, "message": "need verify", "data": {}})
        assert str(request.url) == api.URL_GENSHEN_NOTE_WIDGET
        return httpx.Response(200, json={"retcode": -100, "message": "登录失效，请重新登录", "data": None})

    client = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(api, "get_game_record", game_record)
    monkeypatch.setattr(api, "get_game_list", game_list)
    status, note = asyncio.run(api.genshin_note(make_account(mystool)))
    assert status.login_expired
    assert note is None


def test_mission_action_expiry_stops_followup_calls(expiry_state, mystool, monkeypatch):
    plan = import_module(f"{mystool.__name__}.command.plan")
    model = import_module(f"{mystool.__name__}.model.common")

    _, account, records, outgoing = expiry_state
    user = plan.UserData(enable_notice=True, accounts={"123": account})
    calls = []

    async def state(_):
        calls.append("state")
        return model.BaseApiStatus(success=True), SimpleNamespace(
            current_myb=0,
            state_dict={
                plan.BaseMission.SIGN: (SimpleNamespace(threshold=1), 0),
                plan.BaseMission.VIEW: (SimpleNamespace(threshold=1), 0),
            },
        )

    class Mission:
        name = "测试分区"

        def __init__(self, account):
            pass

        async def sign(self, user):
            calls.append("sign")
            return model.MissionStatus(login_expired=True), None

        async def read(self):
            calls.append("read")
            return model.MissionStatus(success=True)

    monkeypatch.setattr(plan, "get_missions_state", state)
    monkeypatch.setattr(plan.BaseMission, "available_games", {"BBSMission": Mission})
    asyncio.run(plan.perform_bbs_sign(user, ["owner", "shared"], owner_id="owner"))
    assert calls == ["state", "sign"]
    assert list(records["accounts"][("owner", "123")]["expired_credentials"]) == ["app"]
    assert [user_id for user_id, _ in outgoing] == ["owner", "shared"]


def test_game_sign_info_auth_error_stops_only_web_calls(expiry_state, mystool, monkeypatch):
    plan = import_module(f"{mystool.__name__}.command.plan")
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus
    _, account, records, outgoing = expiry_state
    user = plan.UserData(accounts={"123": account})
    calls = []

    async def game_record(account):
        calls.append("records")
        return status(success=True), []

    class Signer:
        name = "原神"
        en_name = "GenshinImpact"
        has_record = True

        def __init__(self, account, records):
            pass

        async def get_info(self, platform):
            calls.append("info")
            return status(login_expired=True), None

        async def sign(self, platform, **kwargs):
            calls.append("sign")
            return status(success=True), None

    monkeypatch.setattr(plan, "get_game_record", game_record)
    monkeypatch.setattr(plan.BaseGameSign, "available_game_signs", [Signer])
    asyncio.run(plan.perform_game_sign(user, ["owner", "shared"], owner_id="owner"))
    assert calls == ["records", "info"]
    assert list(records["accounts"][("owner", "123")]["expired_credentials"]) == ["web"]
    assert [user_id for user_id, _ in outgoing] == ["owner", "shared"]


@pytest.mark.parametrize("phase", ["create", "verify"])
def test_mission_human_verification_preserves_explicit_auth_error(mystool, monkeypatch, phase):
    api = import_module(f"{mystool.__name__}.api.myb_missions_api")
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus
    client = httpx.AsyncClient

    def require_verification(request):
        return httpx.Response(200, json={"retcode": 1034, "message": "need verify", "data": {}})

    async def create_verification(account):
        if phase == "create":
            return status(login_expired=True), None
        return status(success=True), SimpleNamespace(gt="gt", challenge="challenge")

    async def get_validate(user, gt, challenge):
        return SimpleNamespace()

    async def verify_verification(mmt, result, account):
        return status(login_expired=True)

    monkeypatch.setattr(api.httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(require_verification)))
    monkeypatch.setattr(api, "create_verification", create_verification)
    monkeypatch.setattr(api, "get_validate", get_validate)
    monkeypatch.setattr(api, "verify_verification", verify_verification)
    mission = api.BBSMission(make_account(mystool))
    result, points = asyncio.run(mission.sign(SimpleNamespace(geetest_url="enabled"), retry=False))
    assert result.login_expired
    assert not result.need_verify
    assert points is None


def test_app_refresh_resumes_at_next_existing_scheduled_run(mystool, monkeypatch):
    package = mystool.__name__
    plan = import_module(f"{package}.command.plan")
    expiry = import_module(f"{package}.services.credential_expiry")
    login = import_module(f"{package}.services.login")
    passport = import_module(f"{package}.api.passport")
    storage = import_module(f"{package}.storage.session")
    model = import_module(f"{package}.model.common")
    data = import_module(f"{package}.model.data")
    account = make_account(mystool)
    account.enable_game_sign = False
    account.web_scanned_at = int(datetime(2026, 11, 27, 17, tzinfo=timezone.utc).timestamp())
    calls = []
    notices = []

    async def missions(account):
        calls.append(account.cookies.stoken_v2)
        if account.cookies.stoken_v2 == "v2_app":
            return model.BaseApiStatus(login_expired=True), None
        return model.BaseApiStatus(success=True), SimpleNamespace(current_myb=10, state_dict={})

    async def private_message(user_id, message):
        notices.append((user_id, message))
        return True, None

    monkeypatch.setattr(plan, "get_missions_state", missions)
    monkeypatch.setattr(plan, "send_private_msg", private_message)
    monkeypatch.setattr(expiry, "send_private_msg", private_message)

    async def scenario():
        monkeypatch.setattr(storage, "_ready", asyncio.Event())
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": [f"{package}.storage.models"]})
        repository = storage.Repository
        try:
            await Tortoise.generate_schemas()
            await storage.initialize_storage()
            user = data.UserData(accounts={"123": account})
            saved = storage.encode(data.PluginData(users={"owner": user}, user_bind={"shared": "owner"}))
            await repository.commit(await repository.load(), saved)

            await plan.daily_schedule()
            await plan.daily_schedule()
            assert calls == ["v2_app"]
            assert [user_id for user_id, _ in notices] == ["owner", "shared"]
            assert all("登录 App』后按序号" in message and "123" not in message for _, message in notices)

            target = login.select_login(await repository.load(), "shared", "123")
            app = passport.AppCredentials(uid="123", mid="mid", stoken="v2_fresh")
            await repository.update(lambda records: login.write_login(
                records, target, app, None, "ios", None, max_users=0,
            ))
            assert calls == ["v2_app"]
            await plan.daily_schedule()
            assert calls == ["v2_app", "v2_fresh", "v2_fresh"]
            assert len([msg for _, msg in notices if "登录 App』后按序号" in msg]) == 2
            assert len([msg for _, msg in notices if msg.endswith("2026年12月28日过期。")]) == 2
            current = (await repository.load())["accounts"][("owner", "123")]
            assert current["web_scanned_at"] == account.web_scanned_at
            assert current["enable_mission"]
            assert not current["enable_game_sign"]
            assert not current["expired_credentials"]
        finally:
            await Tortoise.close_connections()

    asyncio.run(scenario())


@pytest.mark.parametrize("has_web_scan", [True, False])
def test_scheduled_myb_summary_ends_with_estimated_web_token_expiry(
    mystool, expiry_state, monkeypatch, has_web_scan,
):
    plan = import_module(f"{mystool.__name__}.command.plan")
    status = import_module(f"{mystool.__name__}.model.common").BaseApiStatus
    _, account, _, outgoing = expiry_state
    timestamp = int(datetime(2026, 11, 27, 17, tzinfo=timezone.utc).timestamp())
    account = account.copy(update={
        "community_nickname": "星夜", "mission_games": [],
        "web_scanned_at": timestamp if has_web_scan else None,
    })
    user = plan.UserData(accounts={"123": account})

    async def missions(_account):
        return status(success=True), SimpleNamespace(
            current_myb=100, state_dict={plan.BaseMission.SIGN: (SimpleNamespace(threshold=1), 0)},
        )

    async def send_private(user_id, message):
        outgoing.append((user_id, message))
        return True, None

    monkeypatch.setattr(plan, "get_missions_state", missions)
    monkeypatch.setattr(plan, "send_private_msg", send_private)
    asyncio.run(plan.perform_bbs_sign(user, ["owner"], owner_id="owner"))
    assert len(outgoing) == 1
    summary = outgoing[0][1].splitlines()
    assert summary[:3] == ["⚠️今日米游币任务未全部完成", "🆔账户 星夜", "📅签到：✕"]
    if has_web_scan:
        assert summary[-1] == "🔔网页 Token 预计于 2026年12月28日过期。"
    else:
        assert summary[-1] == "🔔网页 Token 到期日尚未记录，完成一次 Web 扫码后会显示。"
