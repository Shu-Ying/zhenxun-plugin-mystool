import asyncio
from datetime import date, datetime, timezone
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

    monkeypatch.setattr(api, "get_bound_game_roles", failed_record)
    status, note = asyncio.run(getattr(api, note_api)(make_account(mystool)))
    assert note is None
    assert status.game_record_failed
    assert getattr(status, flag)


def test_genshin_widget_preserves_explicit_auth_error(mystool, monkeypatch):
    api = import_module(f"{mystool.__name__}.api.common")

    async def game_record(account):
        return api.BaseApiStatus(success=True), [SimpleNamespace(game_id=2, game_role_id="123", region="cn_gf01")]

    def respond(request):
        if request.url.path.endswith("/dailyNote"):
            return httpx.Response(200, json={"retcode": 1034, "message": "need verify", "data": {}})
        assert str(request.url) == api.URL_GENSHEN_NOTE_WIDGET
        return httpx.Response(200, json={"retcode": -100, "message": "登录失效，请重新登录", "data": None})

    client = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(api, "get_bound_game_roles", game_record)
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
            current_myb=0, unconfirmed={},
            state_dict={
                plan.BaseMission.SIGN: (SimpleNamespace(threshold=1), 0),
                plan.BaseMission.VIEW: (SimpleNamespace(threshold=1), 0),
            },
        )

    class Mission:
        gids = 1
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

    record = SimpleNamespace(game_id=2, nickname="测试", region_name="测试区", game_role_id="123", level=10)

    async def game_record(account):
        calls.append("records")
        return status(success=True), [record]

    class Signer:
        name = "原神"
        en_name = "GenshinImpact"
        game_id = 2

        def __init__(self, account, records):
            self.record = records[0]

        async def get_info(self, platform):
            calls.append("info")
            return status(login_expired=True), None

        async def sign(self, platform, **kwargs):
            calls.append("sign")
            return status(success=True), None

    monkeypatch.setattr(plan, "get_bound_game_roles", game_record)
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
        return model.BaseApiStatus(success=True), model.MissionState(current_myb=10, state_dict={})

    async def private_message(user_id, message):
        notices.append((user_id, message))
        return True, None

    async def board_sign(self, user):
        return model.MissionStatus(success=True), 0

    mission = import_module(f"{package}.api.myb_missions_api")
    monkeypatch.setattr(mission.BBSMission, "sign", board_sign)
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
            current_myb=100, unconfirmed={}, state_dict={plan.BaseMission.SIGN: (SimpleNamespace(threshold=1), 0)},
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



@pytest.mark.parametrize("action", ["read", "like"])
def test_mission_actions_stop_on_empty_feed(mystool, monkeypatch, action):
    api = import_module(f"{mystool.__name__}.api.myb_missions_api")
    mission = api.BBSMission(make_account(mystool))
    attempts = []

    async def empty_posts(retry):
        attempts.append(retry)
        assert len(attempts) == 1
        return api.BaseApiStatus(success=True), []

    monkeypatch.setattr(mission, "get_posts", empty_posts)
    status = asyncio.run(getattr(mission, action)(retry=False))
    assert status.failed_getting_post and not status


def test_bbs_keeps_board_receipts_with_no_account_progress(expiry_state, mystool, monkeypatch):
    _, account, _, _ = expiry_state
    plan = import_module(f"{mystool.__name__}.command.plan")
    model = import_module(f"{mystool.__name__}.model.common")
    account.mission_games = ["BoardOne", "BoardTwo"]
    calls, messages = [], []

    async def unavailable(_):
        return model.BaseApiStatus(network_error=True), None

    class BoardOne:
        name, gids = "板块一", 1

        def __init__(self, account):
            pass

        async def sign(self, user):
            calls.append(self.gids)
            return model.MissionStatus(success=True), 0

    class BoardTwo(BoardOne):
        name, gids = "板块二", 2

    async def send(message):
        messages.append(message)

    monkeypatch.setattr(plan, "get_missions_state", unavailable)
    monkeypatch.setattr(plan.BaseMission, "available_games", {"BoardOne": BoardOne, "BoardTwo": BoardTwo})
    user = plan.UserData(accounts={account.bbs_uid: account})
    asyncio.run(plan.perform_bbs_sign(user, [], matcher=SimpleNamespace(send=send), owner_id="owner"))
    assert calls == [1, 2]
    assert "板块一签到：✓" in messages[0] and "板块二签到：✓" in messages[0]
    assert "已完成今日" not in messages[0] and "当前米游币: 暂未查到" in messages[0]


def test_sign_result_survives_empty_award_calendar(expiry_state, mystool, monkeypatch):
    _, account, _, outgoing = expiry_state
    plan = import_module(f"{mystool.__name__}.command.plan")
    model = import_module(f"{mystool.__name__}.model.common")
    user = plan.UserData(accounts={account.bbs_uid: account})
    role = model.GameRecord(region_name="测试区", game_id=2, level=20, region="cn_gf01",
                            game_role_id="321", nickname="旅行者")

    async def roles(_):
        return model.BaseApiStatus(success=True), [role]

    async def send(user_id, message):
        outgoing.append((user_id, str(message)))

    class Signer:
        name, en_name, game_id = "原神", "GenshinImpact", 2

        def __init__(self, account, records):
            self.record = records[0]

        async def get_info(self, platform):
            return model.BaseApiStatus(success=True), model.GameSignInfo(
                is_sign=True, total_sign_day=0, sign_cnt_missed=0,
            )

        async def get_rewards(self):
            return model.BaseApiStatus(success=True), []

    monkeypatch.setattr(plan, "get_bound_game_roles", roles)
    monkeypatch.setattr(plan, "send_private_msg", send)
    monkeypatch.setattr(plan.BaseGameSign, "available_game_signs", [Signer])
    asyncio.run(plan.perform_game_sign(user, ["owner"], owner_id="owner"))
    assert len(outgoing) == 1
    assert "已经签到过了" in outgoing[0][1] and "奖励暂未查到" in outgoing[0][1]


def test_estimated_web_reminder_retries_definite_failure_and_dedups_app_refresh(expiry_state, monkeypatch):
    expiry, _, records, _ = expiry_state
    current = records["accounts"][("owner", "123")]
    current["web_scanned_at"] = int(datetime(2026, 11, 27, 17, tzinfo=timezone.utc).timestamp())
    sends = []
    records["users"]["other"]["enable_notice"] = False

    async def load():
        return records

    async def send(user_id, message):
        sends.append((user_id, message))
        if len(sends) == 1:
            return False, expiry.PrivateMessageUnavailable("no bot")
        if user_id == "shared":
            return False, TimeoutError()
        return True, None

    monkeypatch.setattr(expiry.Repository, "load", load)
    monkeypatch.setattr(expiry, "send_private_msg", send)

    async def run():
        await expiry.send_estimated_web_expiry_reminders(today=date(2026, 12, 24))
        assert not sends
        await expiry.send_estimated_web_expiry_reminders(today=date(2026, 12, 25))
        current["credential_versions"]["app"] = 1
        await expiry.send_estimated_web_expiry_reminders(today=date(2026, 12, 28))
        await expiry.send_estimated_web_expiry_reminders(today=date(2026, 12, 29))

    asyncio.run(run())
    assert [owner for owner, _ in sends] == ["owner", "shared", "owner"]
    assert all("2026年12月28日" in message for _, message in sends)



@pytest.mark.parametrize("identified", [False, True])
def test_daily_note_widget_needs_matching_role_with_multiple_bound_roles(expiry_state, mystool, monkeypatch, identified):
    _, account, _, _ = expiry_state
    api = import_module(f"{mystool.__name__}.api.common")
    plan = import_module(f"{mystool.__name__}.command.plan")
    first = api.GameRecord(game_id=2, game_role_id="1111", region="cn_gf01",
                           region_name="天空岛", nickname="旅行者", level=60)
    second = first.copy(update={"region": "cn_qd01", "game_role_id": "2222"})
    account.game_sign_roles = {"GenshinImpact": [(second.region, second.game_role_id)]}
    calls = []

    async def roles(_):
        return api.BaseApiStatus(success=True), [first, second]

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("/dailyNote"):
            return httpx.Response(200, json={"retcode": 1034, "data": {}})
        assert request.url.path.endswith("/widget/v2")
        data = {"current_resin": 200}
        if identified:
            data["role"] = {"role_id": second.game_role_id, "server": second.region}
        return httpx.Response(200, json={"retcode": 0, "data": data})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(plan, "get_bound_game_roles", roles)

    notes = asyncio.run(plan._account_notes(account, plan.GenshinImpactSign, "owner", None, {}))
    assert calls[0].url.params["role_id"] == second.game_role_id
    assert "stoken" not in calls[0].headers["cookie"] and "stoken" in calls[1].headers["cookie"]
    assert calls[1].headers["x-rpc-device_id"] == "ios"
    if identified:
        assert [role.game_role_id for role, _ in notes] == [second.game_role_id]
    else:
        assert not notes



def test_anonymous_post_feed_cannot_expire_account_app_credentials(mystool, monkeypatch):
    api = import_module(f"{mystool.__name__}.api.myb_missions_api")
    client = httpx.AsyncClient

    def respond(request):
        assert "cookie" not in request.headers
        return httpx.Response(200, json={"retcode": -100, "message": "登录失效", "data": None})

    monkeypatch.setattr(httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    mission = api.BBSMission(make_account(mystool))
    status = asyncio.run(mission.read(retry=False))
    assert not status and status.failed_getting_post and not status.login_expired



def test_web_role_lookup_uses_only_web_v2_credentials(mystool, monkeypatch):
    api = import_module(f"{mystool.__name__}.api.common")
    account = make_account(mystool)
    account.cookies.update({
        "ltoken_v2": "fixture-web-token", "cookie_token_v2": "fixture-cookie",
        "ltmid_v2": "fixture-mid", "account_mid_v2": "fixture-mid",
    })
    client = httpx.AsyncClient

    def respond(request):
        assert str(request.url) == api.URL_BOUND_GAME_ROLES
        cookie = request.headers["cookie"]
        assert "ltoken_v2=fixture-web-token" in cookie and "stoken=" not in cookie
        return httpx.Response(200, json={"retcode": 0, "data": {"list": [
            {"game_biz": "hk4e_cn", "game_uid": "1001", "region": "cn_gf01",
             "region_name": "天空岛", "nickname": "旅行者", "level": 60},
            {"game_biz": "hk4e_bilibili", "game_uid": "1001", "region": "cn_qd01",
             "region_name": "世界树", "nickname": "旅行者", "level": 20},
        ]}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda: client(transport=httpx.MockTransport(respond)))
    status, roles = asyncio.run(api.get_bound_game_roles(account))
    assert status and [(role.game_id, role.region, role.game_role_id) for role in roles] == [
        (2, "cn_gf01", "1001"), (2, "cn_qd01", "1001"),
    ]
