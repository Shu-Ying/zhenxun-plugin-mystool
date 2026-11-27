import json
from dataclasses import dataclass

from ..model.data import BBSCookies, UserAccount, UserData
from ..utils import generate_device_id


class LoginConflict(RuntimeError):
    """The scanned identity or the current account no longer matches this login."""


def account_version(account):
    return (
        account.get("device_id_ios"), account.get("device_id_android"),
        dict(account.get("credential_versions") or {}),
    )


@dataclass(frozen=True)
class LoginTarget:
    actor: str
    owner: str
    owner_uuid: str | None
    uid: str | None
    version: tuple | None
    device_id: str
    need_app: bool
    need_web: bool


def select_login(records, actor: str, uid: str | None, *, refresh: str | None = None) -> LoginTarget:
    owner = records["bindings"].get(actor, actor)
    owner_uuid = records["users"].get(owner, {}).get("uuid")
    if uid is None:
        return LoginTarget(actor, owner, owner_uuid, None, None, generate_device_id(), True, True)
    account = records["accounts"].get((owner, uid))
    if account is None:
        raise LoginConflict("该米游社账号尚未绑定，请选择自己的账号或新增。")
    cookies = BBSCookies.parse_obj(account.get("cookies") or {})
    expired = account.get("expired_credentials") or {}
    need_app = not cookies.has_credentials("app") or "app" in expired
    need_web = not need_app or not cookies.has_credentials("web") or "web" in expired
    if refresh is not None:
        need_app, need_web = refresh == "app", refresh == "web"
    return LoginTarget(
        actor, owner, owner_uuid, uid, account_version(account),
        account.get("device_id_ios") or generate_device_id(), need_app, need_web,
    )


def login_cookies(uid: str, existing: dict, app, web) -> dict:
    cookies = existing.copy()
    app_mid = app.mid if app else cookies.get("mid")
    web_mid = web.mid if web else cookies.get("ltmid_v2")
    if app_mid and web_mid and app_mid != web_mid:
        raise LoginConflict("两套凭据的账号标识不一致，请核对米游社登录账号。")
    cookies.update(stuid=uid, ltuid=uid, account_id=uid, login_uid=uid)
    if app:
        cookies.update(stoken_v1=None, stoken_v2=app.stoken, mid=app.mid)
    if web:
        cookies.update(
            ltuid_v2=uid, account_id_v2=uid, ltmid_v2=web.mid, account_mid_v2=web.mid,
            ltoken_v2=web.ltoken, cookie_token_v2=web.cookie_token, login_ticket=web.login_ticket,
            ltoken=None, cookie_token=None,
        )
    return cookies


def write_login(
    records, target: LoginTarget, app, web, device_id: str, device_fp: str | None,
    *, max_users: int, community_nickname: str | None = None, web_scanned_at: int | None = None,
) -> str:
    owner = records["bindings"].get(target.actor, target.actor)
    owner_data = records["users"].get(owner)
    if owner != target.owner or (owner_data or {}).get("uuid") != target.owner_uuid:
        raise LoginConflict("扫码期间用户绑定关系发生变化，请重新发起登录。")

    if app is None and web is None or target.uid is None and (app is None or web is None):
        raise LoginConflict("请完成两步扫码后再绑定。")
    uid = target.uid or app.uid
    if (app and app.uid != uid) or (web and web.uid != uid):
        raise LoginConflict("你扫描的米游社账号与本次登录选择的账号不一致。")

    key = (owner, uid)
    record = records["accounts"].get(key)
    if target.uid is None and record is not None:
        raise LoginConflict("该账号已经绑定，请选择该账号进行续登。")
    if target.uid is not None and (record is None or account_version(record) != target.version):
        raise LoginConflict("扫码期间账号已删除或凭据已变更，请重新发起登录。")
    if owner_data is None and max_users > 0 and len(records["users"]) >= max_users:
        raise LoginConflict("机器人已达到用户数上限，已有账号仍可续登。")

    cookies = login_cookies(uid, (record or {}).get("cookies") or {}, app, web)
    if record is None:
        account = UserAccount(
            cookies=BBSCookies.parse_obj(cookies), device_id_ios=device_id,
            device_id_android=generate_device_id(), device_fp=device_fp,
        )
        record = json.loads(account.json(exclude={"community_nickname", "web_scanned_at"}))
    else:
        record["cookies"] = cookies
        if app and (device_fp or device_id != record.get("device_id_ios")):
            record.update(device_id_ios=device_id, device_fp=device_fp)
    if community_nickname:
        record["community_nickname"] = community_nickname
    if web is not None and web_scanned_at is not None:
        record["web_scanned_at"] = web_scanned_at

    versions = record.setdefault("credential_versions", {})
    expired = record.setdefault("expired_credentials", {})
    notices = record.setdefault("credential_expiry_notices", {})
    for family, credentials in (("app", app), ("web", web)):
        if credentials:
            versions[family] = versions.get(family, 0) + 1
            expired.pop(family, None)
            notices.pop(family, None)
    record["credential_expiry_notices"] = {
        family: recipients for family, recipients in notices.items() if not family.startswith("combined:")
    }
    if owner_data is None:
        records["users"][owner] = json.loads(UserData().json(exclude={"accounts", "exchange_plans"}))
    records["accounts"][key] = record
    return uid
