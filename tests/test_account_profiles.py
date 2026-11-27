import asyncio
import hashlib
import json
from importlib import import_module

import httpx
import pytest
from nonebot.adapters.onebot.v11 import Message
from tortoise import Tortoise


DEVICE_ID = "64561CE4-5F43-41D7-B92F-41CEFABC7ABF"


def test_legacy_account_display_uses_neutral_placeholder(mystool):
    models = import_module(f"{mystool.__name__}.model.data")
    account = models.UserAccount(cookies={"stuid": "42"}, phone_number="12345678901")

    assert account.display_name == "昵称待更新"


@pytest.mark.parametrize(
    ("nickname", "expected"),
    [
        ("  白\r\n露\u202e  ", "白 露"),
        ("  [CQ:at,qq=all]  ", "［CQ:at,qq=all］"),
        ("UID 42", "昵称待更新"),
        ("\t\u202e", "昵称待更新"),
        (42, "昵称待更新"),
    ],
)
def test_display_normalizes_community_name(mystool, nickname, expected):
    models = import_module(f"{mystool.__name__}.model.data")
    account = models.UserAccount(cookies={"stuid": "42"}, community_nickname=nickname)

    assert account.display_name == expected


def test_display_uses_text_when_message_completes_a_cq_marker(mystool):
    models = import_module(f"{mystool.__name__}.model.data")
    account = models.UserAccount(cookies={"stuid": "42"}, community_nickname="[CQ:at,qq=all")

    message = Message(f"[账号 {account.display_name}]")

    assert all(segment.type == "text" for segment in message)


@pytest.mark.parametrize("nested", [False, True])
def test_profile_requests_the_bound_community_account(mystool, monkeypatch, nested):
    profile = import_module(f"{mystool.__name__}.api.profile")
    models = import_module(f"{mystool.__name__}.model.data")
    cookies = models.BBSCookies(stuid="42", ltoken="test-web-token")

    def respond(request):
        assert request.method == "GET"
        assert str(request.url) == "https://bbs-api.miyoushe.com/user/api/getUserFullInfo?uid=42"
        assert request.headers["host"] == "bbs-api.miyoushe.com"
        assert request.headers["x-rpc-client_type"] == "2"
        assert request.headers["x-rpc-device_id"] == DEVICE_ID
        assert "ltoken=test-web-token" in request.headers["cookie"]
        timestamp, nonce, signature = request.headers["DS"].split(",")
        digest = hashlib.md5(
            f"salt=t0qEgfub6cvueAPgR5m9aQWWVciEer7v&t={timestamp}&r={nonce}&b=&q=uid=42".encode()
        ).hexdigest()
        assert signature == digest
        details = {"user_info": {"uid": "42", "nickname": "  白露  ", "avatar_url": ""}}
        data = {"user": details} if nested else details
        return httpx.Response(200, json={"retcode": 0, "message": "OK", "data": data})

    monkeypatch.setattr(
        profile, "AsyncClient", lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond))
    )

    assert asyncio.run(profile.fetch_community_nickname("42", DEVICE_ID, cookies)) == "白露"


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (503, "Service unavailable"),
        (200, "Unexpected HTML"),
        (200, '{"retcode": -100, "data": {"user_info": {"uid": "42", "nickname": "白露"}}}'),
        (200, '{"retcode": 0, "data": {"user_info": {"uid": "99", "nickname": "陌生人"}}}'),
        (200, '{"retcode": 0, "data": {"user_info": {"uid": "42", "nickname": 123}}}'),
    ],
)
def test_profile_ignores_unusable_responses(mystool, monkeypatch, status, body):
    profile = import_module(f"{mystool.__name__}.api.profile")
    models = import_module(f"{mystool.__name__}.model.data")
    cookies = models.BBSCookies(stuid="42", stoken="test-app-token")
    monkeypatch.setattr(
        profile,
        "AsyncClient",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body))
        ),
    )

    assert asyncio.run(profile.fetch_community_nickname("42", DEVICE_ID, cookies)) is None


def test_profile_does_not_request_a_uid_outside_its_cookies(mystool, monkeypatch):
    profile = import_module(f"{mystool.__name__}.api.profile")
    models = import_module(f"{mystool.__name__}.model.data")
    cookies = models.BBSCookies(stuid="42", stoken="test-app-token")

    def reject_request():
        pytest.fail("Profile lookup opened a client for a UID outside its cookies")

    monkeypatch.setattr(profile, "AsyncClient", reject_request)

    assert asyncio.run(profile.fetch_community_nickname("99", DEVICE_ID, cookies)) is None


def test_account_label_stays_stable_when_display_name_changes(mystool):
    profiles = import_module(f"{mystool.__name__}.services.account_profiles")
    models = import_module(f"{mystool.__name__}.model.data")
    account = models.UserAccount(cookies={"stuid": "123456789"}, device_id_android=DEVICE_ID)
    renamed = account.copy(update={"community_nickname": "白露"})
    other = account.copy(update={"cookies": models.BBSCookies(stuid="123456788")})

    label = profiles.account_label(account)
    assert label == profiles.account_label(renamed)
    assert label != profiles.account_label(other)
    assert account.bbs_uid not in label
    assert DEVICE_ID not in label


def test_decoded_accounts_disambiguate_collisions_without_changing_stored_json(mystool):
    package = mystool.__name__
    models = import_module(f"{package}.model.data")
    codec = import_module(f"{package}.storage.codec")
    profiles = import_module(f"{package}.services.account_profiles")
    records = {"users": {}, "accounts": {}, "plans": {}, "bindings": {}}

    for owner in ("owner", "other"):
        records["users"][owner] = json.loads(
            models.UserData().json(exclude={"accounts", "exchange_plans"})
        )
    for owner, uid, nickname in (
        ("owner", "42", "Luna"),
        ("owner", "43", "luna"),
        ("owner", "44", None),
        ("other", "45", "Luna"),
    ):
        account = models.UserAccount(
            cookies={"stuid": uid}, device_id_android=DEVICE_ID, community_nickname=nickname,
        )
        stored = json.loads(account.json(exclude={"web_scanned_at", "game_sign_roles"}))
        if nickname is None:
            stored.pop("community_nickname")
        records["accounts"][owner, uid] = stored

    data = codec.decode(records)
    owned = data.users["owner"].accounts

    assert owned["42"].display_name == f"Luna（{profiles.account_label(owned['42'])}）"
    assert owned["43"].display_name == f"luna（{profiles.account_label(owned['43'])}）"
    assert owned["44"].display_name == "昵称待更新"
    assert data.users["other"].accounts["45"].display_name == "Luna"
    assert codec.encode(data) == records


def test_backfill_persists_only_missing_owned_nicknames(mystool, monkeypatch):
    package = mystool.__name__
    profiles = import_module(f"{package}.services.account_profiles")
    models = import_module(f"{package}.model.data")
    repository = import_module(f"{package}.storage.repository").Repository
    retrieved = []

    async def lookup(uid, device_id, cookies):
        retrieved.append(uid)
        assert cookies.bbs_uid == uid
        assert device_id == DEVICE_ID
        if uid == "45":
            raise RuntimeError("unexpected profile client error")
        return "白露"

    def seed(records):
        records["users"].update({
            "owner": {"uuid": "owner-uuid"},
            "other": {"uuid": "other-uuid"},
        })
        records["bindings"]["alias"] = "owner"
        for owner, uid, nickname in (
            ("owner", "42", None),
            ("owner", "43", "景元"),
            ("other", "44", None),
            ("owner", "45", None),
        ):
            account = models.UserAccount(
                cookies={"stuid": uid, "ltoken": "test-web-token"},
                device_id_android=DEVICE_ID,
                community_nickname=nickname,
            )
            records["accounts"][owner, uid] = json.loads(account.json(exclude_none=True))

    async def exercise():
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": [f"{package}.storage.models"]},
        )
        try:
            await Tortoise.generate_schemas()
            await repository.initialize()
            await repository.update(seed)
            refreshed = await profiles.refresh_account_profiles(await repository.load(), "alias")
            stored = await repository.load()
        finally:
            await Tortoise.close_connections()

        assert sorted(retrieved) == ["42", "45"]
        assert stored == refreshed
        accounts = stored["accounts"]
        assert accounts["owner", "42"]["community_nickname"] == "白露"
        assert accounts["owner", "43"]["community_nickname"] == "景元"
        assert "community_nickname" not in accounts["other", "44"]
        assert "community_nickname" not in accounts["owner", "45"]

    monkeypatch.setattr(profiles, "fetch_community_nickname", lookup)
    asyncio.run(exercise())


@pytest.mark.parametrize("new_nickname", [None, "新昵称"])
def test_backfill_ignores_lookup_after_concurrent_renewal(mystool, monkeypatch, new_nickname):
    package = mystool.__name__
    profiles = import_module(f"{package}.services.account_profiles")
    models = import_module(f"{package}.model.data")
    repository = import_module(f"{package}.storage.repository").Repository

    def seed(records):
        records["users"]["owner"] = {"uuid": "owner-uuid"}
        account = models.UserAccount(
            cookies={"stuid": "42", "ltoken": "old-token"}, device_id_android=DEVICE_ID,
        )
        records["accounts"]["owner", "42"] = json.loads(account.json(exclude_none=True))

    def renew(records):
        account = records["accounts"]["owner", "42"]
        account["cookies"]["ltoken"] = "new-token"
        account["community_nickname"] = new_nickname

    async def lookup(uid, device_id, cookies):
        await repository.update(renew)
        return "旧昵称"

    async def exercise():
        await Tortoise.init(
            db_url="sqlite://:memory:",
            modules={"models": [f"{package}.storage.models"]},
        )
        try:
            await Tortoise.generate_schemas()
            await repository.initialize()
            await repository.update(seed)
            refreshed = await profiles.refresh_account_profiles(await repository.load(), "owner")
            stored = await repository.load()
        finally:
            await Tortoise.close_connections()

        assert refreshed == stored
        account = stored["accounts"]["owner", "42"]
        assert account["cookies"]["ltoken"] == "new-token"
        assert account["community_nickname"] == new_nickname

    monkeypatch.setattr(profiles, "fetch_community_nickname", lookup)
    asyncio.run(exercise())
