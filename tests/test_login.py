import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

from nonebot.exception import FinishedException
import pytest
from tortoise import Tortoise


def test_web_cookie_record_survives_persistence_and_stays_separate_from_app(mystool):
    model = import_module(f"{mystool.__name__}.model.data")
    original = model.BBSCookies(
        stuid="42", stoken_v2="v2_app", mid="app-mid",
        ltoken_v2="v2_web", cookie_token_v2="v2_cookie",
        ltuid_v2="42", account_id_v2="42", ltmid_v2="web-mid", account_mid_v2="web-mid",
    )
    restored = model.BBSCookies.parse_raw(original.json())

    assert restored.request_cookies("app") == {"stuid": "42", "stoken": "v2_app", "mid": "app-mid"}
    assert restored.request_cookies("web") == {
        "ltoken_v2": "v2_web", "cookie_token_v2": "v2_cookie",
        "ltuid_v2": "42", "account_id_v2": "42", "ltmid_v2": "web-mid", "account_mid_v2": "web-mid",
    }


def test_legacy_web_cookies_keep_the_v1_names(mystool):
    model = import_module(f"{mystool.__name__}.model.data")
    cookies = model.BBSCookies(stuid="42", ltoken="legacy-ltoken", cookie_token="legacy-cookie")
    assert cookies.request_cookies("web") == {
        "ltuid": "42", "account_id": "42", "ltoken": "legacy-ltoken", "cookie_token": "legacy-cookie",
    }


@pytest.fixture
def login_records():
    return {
        "users": {"owner": {"uuid": "owner-generation"}},
        "bindings": {"friend": "owner"},
        "plans": {},
        "accounts": {
            ("owner", "42"): {
                "cookies": {"stuid": "42", "stoken_v2": "v2_original", "mid": "same-mid"},
                "device_id_ios": "old-device", "device_id_android": "android-device",
                "device_fp": "original-fp", "enable_mission": False,
                "expired_credentials": {"web": "old-web", "app": "old-app"},
                "credential_expiry_notices": {"web": ["friend"], "app": ["owner"]},
            }
        },
    }


def test_web_refresh_updates_only_web_credentials(mystool, login_records):
    login = import_module(f"{mystool.__name__}.services.login")
    target = login.select_login(login_records, "friend", "42", refresh="web")
    assert target.need_web and not target.need_app
    web = SimpleNamespace(uid="42", mid="same-mid", ltoken="v2_web", cookie_token="v2_cookie", login_ticket=None)
    login.write_login(login_records, target, None, web, "old-device", None, max_users=1)
    account = login_records["accounts"][("owner", "42")]
    assert account["cookies"]["stoken_v2"] == "v2_original"
    assert account["cookies"]["cookie_token_v2"] == "v2_cookie"
    assert account["device_fp"] == "original-fp"
    assert account["enable_mission"] is False
    assert account["credential_versions"] == {"web": 1}
    assert account["expired_credentials"] == {"app": "old-app"}
    assert account["credential_expiry_notices"] == {"app": ["owner"]}


@pytest.mark.parametrize("change", ["unbind", "delete", "wrong-account", "renewed"])
def test_refresh_cannot_write_after_target_changes(mystool, login_records, change):
    from copy import deepcopy

    login = import_module(f"{mystool.__name__}.services.login")
    target = login.select_login(login_records, "friend", "42")
    if change == "unbind":
        del login_records["bindings"]["friend"]
    if change == "delete":
        del login_records["accounts"][("owner", "42")]
    if change == "renewed":
        login_records["accounts"][("owner", "42")]["credential_versions"] = {"web": 2}
    uid = "99" if change == "wrong-account" else "42"
    web = SimpleNamespace(uid=uid, mid="same-mid", ltoken="v2_web", cookie_token="v2_cookie", login_ticket=None)
    before = deepcopy(login_records)
    with pytest.raises(login.LoginConflict):
        login.write_login(login_records, target, None, web, "old-device", None, max_users=0)
    assert login_records == before


def test_new_binding_requires_two_matching_scans(mystool):
    login = import_module(f"{mystool.__name__}.services.login")
    empty_records = {"users": {}, "bindings": {}, "plans": {}, "accounts": {}}
    target = login.select_login(empty_records, "new-owner", None)
    app = SimpleNamespace(uid="42", mid="one-mid", stoken="v2_app")
    web = SimpleNamespace(uid="99", mid="other-mid", ltoken="v2_web", cookie_token="v2_cookie", login_ticket=None)
    with pytest.raises(login.LoginConflict):
        login.write_login(empty_records, target, app, None, "device", None, max_users=0)
    with pytest.raises(login.LoginConflict):
        login.write_login(empty_records, target, app, web, "device", None, max_users=0)
    assert empty_records["users"] == empty_records["accounts"] == {}


def test_login_matcher_stages_two_codes_and_then_renews_with_web_only(mystool, monkeypatch):
    package = mystool.__name__
    command = import_module(f"{package}.command.login")
    passport = import_module(f"{package}.api.passport")
    app_cred = passport.AppCredentials(uid="42", mid="shared-mid", stoken="v2_app")
    web_cred = passport.WebCredentials(uid="42", mid="shared-mid", ltoken="v2_web", cookie_token="v2_cookie")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.wait_app_qr.return_value = app_cred
    client.wait_web_qr.side_effect = [passport.QrExpired(), web_cred, web_cred]
    monkeypatch.setattr(command, "PassportClient", lambda **_: client)
    monkeypatch.setattr(command, "send_qr", AsyncMock())
    monkeypatch.setattr(command, "get_device_fp", AsyncMock(return_value=(True, "saved-fp")))
    event = SimpleNamespace(get_user_id=lambda: "owner")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))

    async def scenario():
        await Tortoise.init(db_url="sqlite://:memory:", modules={"models": [f"{package}.storage.models"]})
        try:
            await Tortoise.generate_schemas()
            await command.Repository.initialize()

            with pytest.raises(FinishedException):
                await command.login_account(event, matcher, "新增")
            first = (await command.Repository.load())["accounts"][("owner", "42")]
            assert first["cookies"]["stoken_v2"] == "v2_app"
            assert first["cookies"]["cookie_token_v2"] == "v2_cookie"
            assert first["credential_versions"] == {"app": 1, "web": 1}
            assert client.create_app_qr.await_count == 1
            assert client.create_web_qr.await_count == 2
            assert "绑定成功" in matcher.finish.await_args.args[0]

            with pytest.raises(FinishedException):
                await command.login_account(event, matcher, "42")
            renewed = (await command.Repository.load())["accounts"][("owner", "42")]
            assert renewed["credential_versions"] == {"app": 1, "web": 2}
            assert renewed["device_fp"] == "saved-fp"
            assert client.create_app_qr.await_count == 1
            assert client.create_web_qr.await_count == 3
            assert client.create_web_qr.await_args.args == (first["device_id_ios"],)
            assert "续登成功" in matcher.finish.await_args.args[0]
        finally:
            await Tortoise.close_connections()

    asyncio.run(scenario())


def test_cancelled_second_scan_leaves_new_account_unwritten(mystool, monkeypatch):
    package = mystool.__name__
    command = import_module(f"{package}.command.login")
    passport = import_module(f"{package}.api.passport")
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.wait_app_qr.return_value = passport.AppCredentials(uid="42", mid="mid", stoken="v2_app")
    client.wait_web_qr.side_effect = passport.QrCancelled
    monkeypatch.setattr(command, "PassportClient", lambda **_: client)
    monkeypatch.setattr(command, "send_qr", AsyncMock())
    monkeypatch.setattr(command.Repository, "load", AsyncMock(return_value={
        "users": {}, "accounts": {}, "bindings": {}, "plans": {},
    }))
    save = AsyncMock()
    monkeypatch.setattr(command.Repository, "update", save)
    event = SimpleNamespace(get_user_id=lambda: "owner")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))

    async def scenario():
        with pytest.raises(FinishedException):
            await command.login_account(event, matcher, "新增")
        save.assert_not_awaited()
        assert "取消扫码" in matcher.finish.await_args.args[0]
        assert "owner" not in command._active_logins

    asyncio.run(scenario())


def test_private_cancel_command_stops_active_scan(mystool, monkeypatch):
    command = import_module(f"{mystool.__name__}.command.login")
    records = {"users": {}, "accounts": {}, "bindings": {}, "plans": {}}
    monkeypatch.setattr(command.Repository, "load", AsyncMock(return_value=records))
    save = AsyncMock()
    monkeypatch.setattr(command.Repository, "update", save)
    event = command.PrivateMessageEvent.model_construct(user_id=123)
    login_matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))
    cancel_matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))

    async def scenario():
        started = asyncio.Event()

        async def wait_for_scan(*_):
            started.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(command, "collect_credentials", wait_for_scan)
        login = asyncio.create_task(command.login_account(event, login_matcher, "新增"))
        await asyncio.wait_for(started.wait(), timeout=2)
        with pytest.raises(FinishedException):
            await command.prepare_login(event, cancel_matcher, command.Message("取消"))
        with pytest.raises(FinishedException):
            await asyncio.wait_for(login, timeout=2)
        assert "已取消扫码" in login_matcher.finish.await_args.args[0]
        save.assert_not_awaited()
        assert "123" not in command._active_logins

    asyncio.run(scenario())


def test_android_sign_does_not_send_a_request_when_app_auth_is_blocked(mystool, monkeypatch):
    sign_api = import_module(f"{mystool.__name__}.api.game_sign_api")
    signer = object.__new__(sign_api.GenshinImpactSign)
    signer.record = SimpleNamespace(region="cn_gf01", game_role_id="123")
    signer.account = SimpleNamespace(device_id_android="android-device")
    prepare_device = AsyncMock(return_value=False)

    def reject_network(*args, **kwargs):
        pytest.fail("Android sign was attempted after App authentication was blocked")

    monkeypatch.setattr(sign_api.httpx, "AsyncClient", reject_network)
    monkeypatch.setattr(sign_api, "generate_ds", lambda **kwargs: "unused-signature")
    status, _ = asyncio.run(signer.sign("android", prepare_device=prepare_device))
    assert not status
    prepare_device.assert_awaited_once()
