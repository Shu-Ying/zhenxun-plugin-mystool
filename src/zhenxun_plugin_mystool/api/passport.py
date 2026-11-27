"""Owner-confirmed QR login for the miHoYo App and Web passport channels."""

import asyncio
from dataclasses import dataclass, field
import re
from time import time
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx


_Channel = Literal["app", "web"]
_HOSTS = {"app": "passport-api.mihoyo.com", "web": "passport-api.miyoushe.com"}
_APP_IDS = {"app": "ddxf5dufpuyo", "web": "bll8iq97cem8"}
_UID_COOKIES = frozenset({
    "account_id", "account_id_v2", "ltuid", "ltuid_v2", "stuid", "login_uid",
})
_MID_COOKIES = frozenset({"mid", "ltmid", "ltmid_v2", "account_mid_v2"})
_TOKEN_KINDS = {1: "stoken", 2: "ltoken", 4: "cookie_token"}
_CHANNEL_TOKENS = {"app": {"stoken"}, "web": {"ltoken", "cookie_token"}}
_TRANSIENT_HTTP_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_TRANSIENT_TRANSPORT_ERRORS = (
    asyncio.TimeoutError,
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)
_MAX_CONSECUTIVE_FAILURES = 3
_COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+")


class PassportError(Exception):
    """A login failure with a user-facing message free of response data."""


class QrExpired(PassportError):
    def __init__(self):
        super().__init__("扫码已超时或二维码已失效，请重新登录。")


class QrCancelled(PassportError):
    def __init__(self):
        super().__init__("你已在米游社取消扫码登录。")


class PassportNetworkError(PassportError):
    def __init__(self, *, retryable: bool = False):
        super().__init__("连接米游社失败，请稍后重试。")
        self.retryable = retryable


class PassportResponseError(PassportError):
    def __init__(self):
        super().__init__("米游社返回的扫码结果不完整或无法识别，请重新登录。")


class PassportRefused(PassportError):
    def __init__(
        self, *, retcode: int | None = None, status_code: int | None = None,
        endpoint: str | None = None,
    ):
        self.retcode = retcode
        self.status_code = status_code
        self.endpoint = endpoint
        if retcode is not None:
            super().__init__(f"米游社拒绝了本次扫码登录（返回码 {retcode}）。")
        elif status_code is not None:
            super().__init__(f"米游社拒绝了本次扫码登录（HTTP {status_code}）。")
        else:
            super().__init__("米游社要求进一步验证，请在米游社内完成验证后重新登录。")


@dataclass(frozen=True)
class PassportQr:
    url: str = field(repr=False)
    ticket: str = field(repr=False)
    device_id: str = field(repr=False)
    deadline: float
    channel: _Channel


@dataclass(frozen=True)
class AppCredentials:
    uid: str
    mid: str = field(repr=False)
    stoken: str = field(repr=False)


@dataclass(frozen=True)
class WebCredentials:
    uid: str
    ltoken: str = field(repr=False)
    cookie_token: str = field(repr=False)
    mid: str = field(repr=False)
    login_ticket: str | None = field(default=None, repr=False)


def _merge_value(values: dict[str, str], name: str, value: object) -> None:
    if value is None or value == "":
        return
    if name == "uid" and type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not _COOKIE_VALUE.fullmatch(value):
        raise PassportResponseError()
    if name == "uid" and not re.fullmatch(r"[1-9][0-9]*", value):
        raise PassportResponseError()
    if name in _TOKEN_KINDS.values() and (not value.startswith("v2_") or len(value) == 3):
        raise PassportResponseError()
    if values.setdefault(name, value) != value:
        raise PassportResponseError()


def _merge_cookie(values: dict[str, str], channel: _Channel, cookie: str) -> None:
    name, separator, value = cookie.partition("=")
    if not separator:
        return
    name = name.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1]
    if name in _UID_COOKIES:
        _merge_value(values, "uid", value)
    elif name in _MID_COOKIES:
        _merge_value(values, "mid", value)
    elif name == "login_ticket" and channel == "web":
        _merge_value(values, "login_ticket", value)
    else:
        kind = name.removesuffix("_v2")
        if kind in _CHANNEL_TOKENS[channel] and (kind != name or value.startswith("v2_")):
            _merge_value(values, kind, value)


def _merge_token(values: dict[str, str], channel: _Channel, token: object) -> None:
    if not isinstance(token, dict):
        return
    token_type = token.get("token_type")
    typed_kind = _TOKEN_KINDS.get(token_type) if type(token_type) is int else None
    name = token.get("name")
    named_kind = name.removesuffix("_v2") if isinstance(name, str) else None
    if named_kind not in _TOKEN_KINDS.values():
        named_kind = None
    kind = typed_kind or named_kind
    if kind not in _CHANNEL_TOKENS[channel]:
        return
    if (token_type is not None and typed_kind is None) or (
        typed_kind is not None and named_kind is not None and typed_kind != named_kind
    ):
        raise PassportResponseError()
    value = token.get("token")
    explicit_v2 = isinstance(name, str) and name.endswith("_v2")
    if not explicit_v2 and isinstance(value, str) and not value.startswith("v2_"):
        return
    _merge_value(values, kind, value)


def _credentials(
    channel: _Channel, data: dict[str, Any], response: httpx.Response
) -> dict[str, str]:
    values: dict[str, str] = {}
    user = data.get("user_info")
    if user is not None:
        if not isinstance(user, dict):
            raise PassportResponseError()
        _merge_value(values, "uid", user.get("aid"))
        _merge_value(values, "mid", user.get("mid"))
    for header in response.headers.get_list("set-cookie"):
        _merge_cookie(values, channel, header.partition(";")[0])
    tokens = data.get("tokens")
    if isinstance(tokens, list):
        for token in tokens:
            _merge_token(values, channel, token)
    _merge_token(values, channel, data.get("token"))
    cookies = data.get("cookies")
    if isinstance(cookies, list):
        for cookie in cookies:
            if isinstance(cookie, str):
                _merge_cookie(values, channel, cookie.partition(";")[0])
            else:
                _merge_token(values, channel, cookie)
    cookie = data.get("cookie")
    if isinstance(cookie, str):
        for part in cookie.split(";"):
            _merge_cookie(values, channel, part)
    if not ({"uid", "mid"} | _CHANNEL_TOKENS[channel]) <= values.keys():
        raise PassportResponseError()
    return values


class PassportClient:
    def __init__(
        self,
        *,
        request_timeout: float = 10,
        poll_interval: float = 1,
        qr_lifetime: float = 120,
        app_version: str = "2.63.1",
        retry_attempts: int = _MAX_CONSECUTIVE_FAILURES,
        retry_interval: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False)
        self._request_timeout = request_timeout
        self._poll_interval = poll_interval
        self._qr_lifetime = qr_lifetime
        self._app_version = app_version
        self._retry_attempts = max(1, retry_attempts)
        self._retry_interval = poll_interval if retry_interval is None else max(0, retry_interval)

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        await self._client.__aexit__(*exc_info)

    async def create_app_qr(self, device_id: str) -> PassportQr:
        return await self._create_qr("app", device_id)

    async def create_web_qr(self, device_id: str) -> PassportQr:
        return await self._create_qr("web", device_id)

    async def wait_app_qr(self, qr: PassportQr) -> AppCredentials:
        values = await self._poll("app", qr)
        return AppCredentials(uid=values["uid"], mid=values["mid"], stoken=values["stoken"])

    async def wait_web_qr(self, qr: PassportQr) -> WebCredentials:
        values = await self._poll("web", qr)
        return WebCredentials(
            uid=values["uid"],
            ltoken=values["ltoken"],
            cookie_token=values["cookie_token"],
            mid=values["mid"],
            login_ticket=values.get("login_ticket"),
        )

    async def _create_qr(self, channel: _Channel, device_id: str) -> PassportQr:
        for attempt in range(self._retry_attempts):
            try:
                data, _ = await self._request(channel, device_id, "createQRLogin", {}, self._request_timeout)
                break
            except PassportNetworkError as error:
                if not error.retryable or attempt + 1 == self._retry_attempts:
                    raise
                await asyncio.sleep(self._retry_interval)
        url, ticket = data.get("url"), data.get("ticket")
        if not isinstance(url, str) or not isinstance(ticket, str) or not _COOKIE_VALUE.fullmatch(ticket):
            raise PassportResponseError()
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc not in {"user.mihoyo.com", "user.miyoushe.com"}:
                raise PassportResponseError()
            query = parse_qs(parsed.query, keep_blank_values=True)
            for name in ("ticket", "tk"):
                if name in query and query[name] != [ticket]:
                    raise PassportResponseError()
            expire = query.get("expire", [""])[0]
            lifetime = self._qr_lifetime
            if expire and re.fullmatch(r"[0-9]{1,12}", expire):
                lifetime = min(lifetime, int(expire) - time())
        except ValueError:
            raise PassportResponseError() from None
        if lifetime <= 0:
            raise QrExpired()
        return PassportQr(
            url, ticket, device_id, asyncio.get_running_loop().time() + lifetime, channel,
        )

    async def _poll(self, channel: _Channel, qr: PassportQr) -> dict[str, str]:
        if qr.channel != channel:
            raise PassportResponseError()
        loop = asyncio.get_running_loop()
        failures = 0
        while (remaining := qr.deadline - loop.time()) > 0:
            try:
                data, response = await self._request(
                    channel,
                    qr.device_id,
                    "queryQRLoginStatus",
                    {"ticket": qr.ticket},
                    min(self._request_timeout, remaining),
                )
            except PassportNetworkError as error:
                if loop.time() >= qr.deadline:
                    raise QrExpired() from None
                failures += 1
                if not error.retryable or failures >= self._retry_attempts:
                    raise
                await asyncio.sleep(max(0, min(self._retry_interval, qr.deadline - loop.time())))
                continue
            else:
                if loop.time() >= qr.deadline:
                    raise QrExpired()
                failures = 0
                status = data.get("status")
                if not isinstance(status, str):
                    raise PassportResponseError()
                status = status.lower()
                realname = data.get("realname_info")
                if data.get("need_realperson") is True or (
                    isinstance(realname, dict) and realname.get("required") is True
                ):
                    raise PassportRefused()
                if status == "confirmed":
                    return _credentials(channel, data, response)
                if status == "expired":
                    raise QrExpired()
                if status in {"cancelled", "canceled"}:
                    raise QrCancelled()
                if status not in {"created", "scanned", "scannedwaitingconfirmation"}:
                    raise PassportResponseError()
            await asyncio.sleep(max(0, min(self._poll_interval, qr.deadline - loop.time())))
        raise QrExpired()

    async def _request(
        self,
        channel: _Channel,
        device_id: str,
        action: str,
        body: dict[str, str],
        timeout: float,
    ) -> tuple[dict[str, Any], httpx.Response]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "x-rpc-app_id": _APP_IDS[channel],
            "x-rpc-app_version": self._app_version,
            "x-rpc-channel": "miyousheluodi",
            "x-rpc-client_type": "3" if channel == "app" else "4",
            "x-rpc-device_id": device_id,
            "x-rpc-sys_version": "12",
            "User-Agent": "HYPContainer/1.3.3.182" if channel == "app" else (
                "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/103.0.5060.129 Mobile Safari/537.36 miHoYoBBS/{self._app_version}"
            ),
        }
        request = self._client.build_request(
            "POST",
            f"https://{_HOSTS[channel]}/account/ma-cn-passport/{channel}/{action}",
            headers=headers,
            json=body,
            timeout=timeout,
        )
        # A QR poll only authenticates the ticket. Keep response cookies out of future requests.
        request.headers.pop("cookie", None)
        try:
            response = await asyncio.wait_for(self._client.send(request), timeout=timeout)
        except _TRANSIENT_TRANSPORT_ERRORS:
            raise PassportNetworkError(retryable=True) from None
        except httpx.RequestError:
            raise PassportNetworkError() from None
        finally:
            self._client.cookies.clear()
        if response.status_code in _TRANSIENT_HTTP_STATUS:
            raise PassportNetworkError(retryable=True)
        endpoint = f"{channel}/{action}"
        if not response.is_success:
            raise PassportRefused(status_code=response.status_code, endpoint=endpoint)
        try:
            decoded = response.json()
        except ValueError:
            raise PassportResponseError() from None
        if not isinstance(decoded, dict) or type(decoded.get("retcode")) is not int:
            raise PassportResponseError()
        retcode = decoded["retcode"]
        if retcode == -3501 or (channel == "app" and action == "queryQRLoginStatus" and retcode == -106):
            raise QrExpired()
        if retcode == -3505:
            raise QrCancelled()
        if retcode != 0:
            raise PassportRefused(retcode=retcode, status_code=response.status_code, endpoint=endpoint)
        data = decoded.get("data")
        if not isinstance(data, dict):
            raise PassportResponseError()
        return data, response
