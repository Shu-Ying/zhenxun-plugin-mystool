"""Drive the passport QR flow against mocked HTTP, outside the bot runtime."""

import asyncio
from importlib import import_module
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import httpx
import pytest


@pytest.fixture(scope="module")
def passport():
    path = Path(__file__).resolve().parents[1] / "src/zhenxun_plugin_mystool/api/passport.py"
    assert path.is_file(), "Passport QR client is missing"
    spec = importlib.util.spec_from_file_location("_mystool_passport_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]


def create_response():
    return httpx.Response(
        200,
        json={
            "retcode": 0,
            "message": "OK",
            "data": {
                "url": "https://user.mihoyo.com/login-platform/mobile.html?tk=private-ticket#/login/qr",
                "ticket": "private-ticket",
            },
        },
    )


def status_response(status, *, headers=(), **data):
    return httpx.Response(
        200,
        headers=headers,
        json={"retcode": 0, "message": "OK", "data": {"status": status, **data}},
    )


def poll(passport, responses, channel="web", **options):
    requests = []
    replies = iter((create_response(), *responses))

    async def handle(request):
        requests.append(request)
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def run():
        async with passport.PassportClient(
            transport=httpx.MockTransport(handle), poll_interval=0, **options
        ) as client:
            if channel == "app":
                qr = await client.create_app_qr("device-id")
                credentials = await client.wait_app_qr(qr)
            else:
                qr = await client.create_web_qr("device-id")
                credentials = await client.wait_web_qr(qr)
        return qr, credentials

    return run, requests


def test_app_releases_only_the_typed_stoken_after_confirmation(passport):
    run, requests = poll(
        passport,
        [
            status_response("Created", tokens=[], user_info=None),
            status_response(
                "ScannedWaitingConfirmation",
                tokens=[{"token_type": 1, "token": "v2_premature"}],
                user_info={"aid": "999", "mid": "premature"},
            ),
            status_response(
                "Confirmed",
                tokens=[
                    {"token_type": 2, "token": "v2_unrelated"},
                    {"token_type": 1, "token": "legacy-stoken"},
                    {"token_type": 1, "token": "v2_app-secret"},
                ],
                user_info={"aid": "1234", "mid": "app-mid"},
            ),
        ],
        "app",
    )

    qr, credentials = asyncio.run(run())

    assert qr.url.startswith("https://user.mihoyo.com/")
    assert "private-ticket" not in repr(qr)
    assert credentials == passport.AppCredentials("1234", "app-mid", "v2_app-secret")
    assert "v2_app-secret" not in repr(credentials)
    assert len(requests) == 4
    assert [request.url.path.rsplit("/", 1)[-1] for request in requests] == [
        "createQRLogin",
        "queryQRLoginStatus",
        "queryQRLoginStatus",
        "queryQRLoginStatus",
    ]
    assert all(request.url.host == "passport-api.mihoyo.com" for request in requests)
    assert all("/app/" in request.url.path for request in requests)
    assert all(request.headers["x-rpc-device_id"] == "device-id" for request in requests)
    assert all(request.headers["x-rpc-app_id"] == "ddxf5dufpuyo" for request in requests)
    assert json.loads(requests[0].content) == {}
    assert json.loads(requests[-1].content) == {"ticket": "private-ticket"}
    assert not requests[-1].url.query


def test_web_reads_only_confirmed_v2_cookies_and_canonical_companions(passport):
    run, requests = poll(
        passport,
        [
            status_response(
                "Created",
                headers=[("set-cookie", "ltoken_v2=v2_premature; Path=/")],
            ),
            status_response(
                "Confirmed",
                headers=[
                    ("set-cookie", "ltoken_v2=v2_web-secret==; Path=/; HttpOnly"),
                    ("set-cookie", "cookie_token_v2=v2_cookie-secret; Path=/; Secure"),
                    ("set-cookie", "ltuid_v2=1234; Path=/"),
                    ("set-cookie", "account_mid_v2=web-mid; Path=/"),
                    ("set-cookie", "login_ticket=web-ticket; Path=/"),
                    ("set-cookie", "tracking=private-tracking; Path=/"),
                ],
                tokens=[],
                user_info={"aid": 1234, "mid": "web-mid"},
            ),
        ],
    )

    _, credentials = asyncio.run(run())

    assert credentials == passport.WebCredentials(
        uid="1234", ltoken="v2_web-secret==", cookie_token="v2_cookie-secret",
        mid="web-mid", login_ticket="web-ticket",
    )
    shown = repr(credentials)
    assert all(secret not in shown for secret in ("secret", "web-ticket", "private-tracking"))
    assert all(request.url.host == "passport-api.miyoushe.com" for request in requests)
    assert all("/web/" in request.url.path for request in requests)
    assert all(request.headers["x-rpc-app_id"] == "bll8iq97cem8" for request in requests)
    assert all("cookie" not in request.headers for request in requests)


@pytest.mark.parametrize(
    "data",
    [
        {"cookie": "account_id_v2=1234; ltmid_v2=web-mid; ltoken_v2=v2_l; cookie_token_v2=v2_c"},
        {
            "cookies": [
                "ltuid_v2=1234; Path=/",
                "account_mid_v2=web-mid; Path=/",
                {"token_type": 2, "token": "v2_l"},
                {"name": "cookie_token", "token": "v2_c"},
            ],
        },
        {
            "tokens": [{"token_type": 2, "token": "v2_l"}, {"token_type": 4, "token": "v2_c"}],
            "user_info": {"aid": "1234", "mid": "web-mid"},
        },
    ],
)
def test_web_accepts_confirmed_inline_response_shapes(passport, data):
    run, _ = poll(passport, [status_response("Confirmed", **data)])

    _, credentials = asyncio.run(run())

    assert credentials == passport.WebCredentials("1234", "v2_l", "v2_c", "web-mid")


@pytest.mark.parametrize(
    "headers, data",
    [
        ([("set-cookie", "ltuid_v2=4321")], {}),
        ([("set-cookie", "account_mid_v2=other-mid")], {}),
        ([("set-cookie", "ltoken_v2=v2_other-secret")], {}),
        ([("set-cookie", "ltoken_v2=legacy-secret")], {}),
        (
            [],
            {"tokens": [{"token_type": 2, "name": "cookie_token", "token": "v2_secret"}]},
        ),
    ],
)
def test_web_rejects_conflicting_or_wrongly_typed_credentials(passport, headers, data):
    run, requests = poll(
        passport,
        [
            status_response(
                "Confirmed",
                headers=headers,
                cookie="account_id_v2=1234; ltmid_v2=web-mid; ltoken_v2=v2_l; cookie_token_v2=v2_c",
                **data,
            ),
        ],
    )

    with pytest.raises(passport.PassportResponseError) as error:
        asyncio.run(run())

    assert "secret" not in str(error.value)
    assert len(requests) == 2


@pytest.mark.parametrize(
    "channel, response",
    [
        (
            "app",
            status_response(
                "Confirmed",
                token={"token_type": 2, "token": "v2_wrong-kind"},
                user_info={"aid": "1234", "mid": "app-mid"},
            ),
        ),
        (
            "web",
            status_response(
                "Confirmed",
                cookie="account_id_v2=1234; ltoken_v2=v2_l; cookie_token_v2=v2_c",
            ),
        ),
        (
            "web",
            status_response(
                "Confirmed",
                cookie="account_id_v2=1234; ltmid_v2=web-mid; ltoken=legacy; cookie_token_v2=v2_c",
            ),
        ),
        (
            "web",
            status_response(
                "Confirmed",
                headers=[("set-cookie", "tracking=x; ltoken_v2=v2_hidden; Path=/")],
                cookie="account_id_v2=1234; ltmid_v2=web-mid; cookie_token_v2=v2_c",
            ),
        ),
    ],
)
def test_confirmation_requires_all_usable_credentials(passport, channel, response):
    run, _ = poll(passport, [response], channel)

    with pytest.raises(passport.PassportResponseError):
        asyncio.run(run())


@pytest.mark.parametrize(
    "response, error_type",
    [
        (httpx.Response(200, json={"retcode": -3501, "message": "secret"}), "QrExpired"),
        (httpx.Response(200, json={"retcode": -3505, "message": "secret"}), "QrCancelled"),
        (httpx.Response(200, json={"retcode": 1034, "message": "secret"}), "PassportRefused"),
        (
            httpx.Response(
                200, json={"retcode": -3001, "message": "secret", "data": {"status": "Confirmed"}},
            ),
            "PassportRefused",
        ),
        (status_response("Confirmed", need_realperson=True), "PassportRefused"),
        (status_response("Unexpected"), "PassportResponseError"),
        (httpx.Response(200, text="secret"), "PassportResponseError"),
        (httpx.Response(403, text="secret"), "PassportRefused"),
    ],
)
def test_terminal_poll_responses_stop_without_a_retry(passport, response, error_type):
    run, requests = poll(passport, [response])

    with pytest.raises(getattr(passport, error_type)) as error:
        asyncio.run(run())

    assert len(requests) == 2
    assert "secret" not in str(error.value)


def test_poll_recovers_from_transient_network_and_http_errors(passport):
    run, requests = poll(
        passport,
        [
            httpx.ConnectError("secret"),
            httpx.Response(503, text="secret"),
            status_response(
                "Confirmed",
                cookie="account_id_v2=1234; ltmid_v2=web-mid; ltoken_v2=v2_l; cookie_token_v2=v2_c",
            ),
        ],
    )

    _, credentials = asyncio.run(run())

    assert credentials.uid == "1234"
    assert len(requests) == 4


def test_poll_abandons_repeated_transient_failures(passport):
    run, requests = poll(passport, [httpx.ReadTimeout("secret") for _ in range(3)])

    with pytest.raises(passport.PassportNetworkError) as error:
        asyncio.run(run())

    assert len(requests) == 4
    assert "secret" not in str(error.value)


def test_poll_honors_configured_retry_limit(passport):
    run, requests = poll(passport, [httpx.ReadTimeout("secret")], retry_attempts=1)

    with pytest.raises(passport.PassportNetworkError):
        asyncio.run(run())

    assert len(requests) == 2


def test_old_app_ticket_expiry_is_still_reported(passport):
    run, _ = poll(passport, [httpx.Response(200, json={"retcode": -106})], channel="app")

    with pytest.raises(passport.QrExpired):
        asyncio.run(run())


def test_in_flight_request_cannot_extend_the_absolute_qr_deadline(passport):
    calls = []

    async def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return create_response()
        await asyncio.Event().wait()

    async def run():
        async with passport.PassportClient(
            transport=httpx.MockTransport(handle), request_timeout=10, qr_lifetime=0.03,
        ) as client:
            qr = await client.create_web_qr("device-id")
            with pytest.raises(passport.QrExpired):
                await asyncio.wait_for(client.wait_web_qr(qr), 1)

    asyncio.run(run())

    assert len(calls) == 2


def test_already_expired_server_ticket_is_never_polled(passport):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "retcode": 0,
                "data": {
                    "url": "https://user.mihoyo.com/login-platform/mobile.html?tk=ticket&expire=1",
                    "ticket": "ticket",
                },
            },
        )

    async def run():
        async with passport.PassportClient(transport=httpx.MockTransport(handle)) as client:
            qr = await client.create_web_qr("device-id")
            await client.wait_web_qr(qr)

    with pytest.raises(passport.QrExpired):
        asyncio.run(run())

    assert len(requests) == 1


@pytest.mark.parametrize(
    "api_name",
    [
        "get_login_ticket_by_captcha",
        "get_cookie_token_by_stoken",
        "get_stoken_v2_by_v1",
        "get_ltoken_by_stoken",
    ],
)
def test_legacy_credential_refusal_logs_only_safe_status(mystool, monkeypatch, api_name):
    api = import_module(f"{mystool.__name__}.api.common")
    log_messages = []
    monkeypatch.setattr(
        api,
        "logger",
        SimpleNamespace(
            debug=log_messages.append, warning=log_messages.append, exception=log_messages.append,
        ),
    )
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={"retcode": -464, "message": "private-token", "data": {"token": "private-token"}},
        )
    )
    client_type = httpx.AsyncClient
    monkeypatch.setattr(api.httpx, "AsyncClient", lambda: client_type(transport=transport))
    if api_name == "get_login_ticket_by_captcha":
        args = ("00000000000", 123456)
    else:
        args = (api.BBSCookies(stuid="1234", stoken_v1="v1", stoken_v2="v2_app", mid="mid"),)

    status, cookies = asyncio.run(getattr(api, api_name)(*args, retry=False))

    assert status.incorrect_return and not status.network_error
    assert cookies is None
    logged = " ".join(log_messages)
    assert "private-token" not in logged
    assert "200" in logged and "-464" in logged
