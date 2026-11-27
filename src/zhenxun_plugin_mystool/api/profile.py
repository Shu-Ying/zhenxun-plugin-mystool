import asyncio

from httpx import AsyncClient, HTTPError

from ..model.config import plugin_config, plugin_env
from ..model.data import BBSCookies, normalize_community_nickname
from ..utils.common import generate_ds
from .common import HEADERS_BBS_VERIFICATION, URL_GET_USER_INFO


async def fetch_community_nickname(uid: str, device_id: str, cookies: BBSCookies) -> str | None:
    """Read the community nickname for the account identified by these cookies."""
    if uid != cookies.bbs_uid or not uid.isascii() or not uid.isdecimal():
        return None
    if not cookies.has_credentials("combined"):
        return None

    url = URL_GET_USER_INFO.format(uid=uid)
    headers = {
        **HEADERS_BBS_VERIFICATION,
        "x-rpc-device_id": device_id,
        "DS": generate_ds(params=f"uid={uid}", salt=plugin_env.salt_config.SALT_DATA, ds2=True),
    }
    timeout = min(plugin_config.preference.timeout, 5)
    try:
        async with AsyncClient() as client:
            response = await asyncio.wait_for(
                client.get(url, headers=headers, cookies=cookies.request_cookies("combined"), timeout=timeout),
                timeout=timeout,
            )
        response.raise_for_status()
        body = response.json()
        if body["retcode"] != 0:
            return None
        data = body["data"]
        info = (data.get("user") or data)["user_info"]
        if str(info["uid"]) == uid:
            return normalize_community_nickname(info["nickname"], uid)
    except (HTTPError, asyncio.TimeoutError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None
