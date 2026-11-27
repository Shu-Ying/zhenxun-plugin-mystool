from datetime import datetime, timedelta
import hashlib
import json

from nonebot import get_driver
from nonebot.adapters.onebot.v11 import ActionFailed
from nonebot_plugin_apscheduler import scheduler
from pytz import timezone

from ..model.data import UserAccount
from ..storage.repository import Repository
from ..utils import COMMAND_BEGIN, logger, send_private_msg
from ..utils.common import PrivateMessageUnavailable


WEB_TOKEN_ESTIMATED_DAYS = 30
_SHANGHAI = timezone("Asia/Shanghai")


def _estimated_web_expiry(scanned_at: int):
    return datetime.fromtimestamp(scanned_at, _SHANGHAI).date() + timedelta(days=WEB_TOKEN_ESTIMATED_DAYS)


def web_token_expiry_notice(web_scanned_at: int | None, *, just_scanned: bool = False) -> str:
    if web_scanned_at is None:
        return "🔔网页 Token 到期日尚未记录，完成一次 Web 扫码后会显示。"
    expiry = _estimated_web_expiry(web_scanned_at)
    date = expiry.strftime("%Y年%m月%d日")
    if just_scanned:
        return f"🔔网页 Token 预计约 {WEB_TOKEN_ESTIMATED_DAYS} 天后过期（{date}），届时请重新扫描网页二维码。"
    return f"🔔网页 Token 预计于 {date}过期。"


def credential_fingerprint(account: UserAccount, family: str) -> str:
    families = ("app", "web") if family == "combined" else (family,)
    values = (
        account.cookies.request_cookies(family),
        {kind: account.credential_versions.get(kind, 0) for kind in families},
    )
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class CredentialExpiry:
    """Track authentication failures for credentials from one account snapshot."""

    def __init__(self, owner_id: str, account: UserAccount, reply=None):
        self.owner_id = owner_id
        self.account = account
        self.reply = reply

    def _login_tip(self, family):
        mode = {"app": " App", "web": " Web", "combined": ""}[family]
        return f"请私聊发送『{COMMAND_BEGIN}登录{mode}』后按序号选择账号续登。"

    def _message(self, family, operation):
        prefix = f"⚠️账户 {self.account.display_name} 执行{operation}时，服务器返回登录失效。"
        if family == "combined":
            return f"{prefix}无法确定失效的是 App 还是网页凭证。{self._login_tip(family)}"
        return f"{prefix}{self._login_tip(family)}"

    def _current_account(self, records, family, version):
        payload = records["accounts"].get((self.owner_id, self.account.bbs_uid))
        if payload is not None:
            current = UserAccount.parse_obj(payload)
            if credential_fingerprint(current, family) == version:
                return payload
        return None

    async def skip(self, family: str, operation: str) -> bool:
        if not self.account.cookies.has_credentials(family):
            if self.reply is not None:
                await self.reply(
                    f"⚠️账户 {self.account.display_name} 缺少{operation}所需凭证。"
                    f"{self._login_tip(family)}"
                )
            return True
        if self.reply is not None or family == "combined":
            return False

        version = credential_fingerprint(self.account, family)

        def check(records):
            current = self._current_account(records, family, version)
            if current is None:
                return None
            return current.get("expired_credentials", {}).get(family) == version

        expired = await Repository.update(check)
        if expired:
            await self._notify(family, version, operation)
        return expired is not False

    async def handle(self, status, family: str, operation: str) -> bool:
        if not status.login_expired:
            return False
        version = credential_fingerprint(self.account, family)

        def record(records):
            current = self._current_account(records, family, version)
            if current is None:
                return False
            if family != "combined":
                expired = current.setdefault("expired_credentials", {})
                if expired.get(family) != version:
                    expired[family] = version
                    current.setdefault("credential_expiry_notices", {}).pop(family, None)
            return True

        current = await Repository.update(record)
        if self.reply is not None:
            await self.reply(self._message(family, operation))
        elif current:
            await self._notify(family, version, operation)
        return True

    async def _notify(self, family, version, operation):
        notice_key = f"combined:{version}" if family == "combined" else family

        def recipients(records):
            owner = records["users"].get(self.owner_id)
            if owner is None or not owner.get("enable_notice", True):
                return []
            return [self.owner_id] + [
                source for source, target in records["bindings"].items() if target == self.owner_id
            ]

        def claim(records, user_id):
            current = self._current_account(records, family, version)
            if current is None or user_id not in recipients(records):
                return False
            if family != "combined" and current.get("expired_credentials", {}).get(family) != version:
                return False
            notices = current.setdefault("credential_expiry_notices", {})
            if family == "combined":
                for key in list(notices):
                    if key.startswith("combined:") and key != notice_key:
                        del notices[key]
            sent = notices.setdefault(notice_key, [])
            if user_id in sent:
                return False
            sent.append(user_id)
            return True

        def release(records, user_id):
            current = self._current_account(records, family, version)
            if current is not None:
                sent = current.get("credential_expiry_notices", {}).get(notice_key, [])
                if user_id in sent:
                    sent.remove(user_id)

        for user_id in await Repository.update(recipients):
            if not await Repository.update(lambda records: claim(records, user_id)):
                continue
            try:
                delivered, _ = await send_private_msg(user_id=user_id, message=self._message(family, operation))
            except Exception as exc:
                logger.warning(f"发送米游社登录失效通知失败：{type(exc).__name__}")
                delivered = False
            if not delivered:
                await Repository.update(lambda records: release(records, user_id))


def _estimate_due(account, today):
    if account.web_scanned_at is None or not account.cookies.has_credentials("web"):
        return False
    version = credential_fingerprint(account, "web")
    if account.expired_credentials.get("web") == version:
        return False
    return 0 <= (_estimated_web_expiry(account.web_scanned_at) - today).days <= 3


def _estimate_recipients(records, owner):
    settings = records["users"].get(owner)
    if settings is None or not settings.get("enable_notice", True):
        return []
    return [owner, *(source for source, target in records["bindings"].items() if target == owner)]


async def send_estimated_web_expiry_reminders(*, today=None):
    today = today or datetime.now(_SHANGHAI).date()
    records = await Repository.load()
    for (owner, uid), raw in records["accounts"].items():
        account = UserAccount.parse_obj(raw)
        if not _estimate_due(account, today):
            continue
        expiry = CredentialExpiry(owner, account)
        version = credential_fingerprint(account, "web")

        def claim(latest, recipient):
            raw_current = expiry._current_account(latest, "web", version)
            if raw_current is None or recipient not in _estimate_recipients(latest, owner):
                return False
            if not _estimate_due(UserAccount.parse_obj(raw_current), today):
                return False
            sent = raw_current.setdefault("credential_expiry_notices", {}).setdefault("web_estimate", [])
            if recipient in sent:
                return False
            sent.append(recipient)
            return True

        def release(latest, recipient):
            raw_current = expiry._current_account(latest, "web", version)
            if raw_current is not None:
                sent = raw_current.get("credential_expiry_notices", {}).get("web_estimate", [])
                if recipient in sent:
                    sent.remove(recipient)

        for recipient in _estimate_recipients(records, owner):
            if not await Repository.update(lambda latest: claim(latest, recipient)):
                continue
            message = (f"账户 {account.display_name}\n{web_token_expiry_notice(account.web_scanned_at)}\n"
                       f"到期后请私聊发送「{COMMAND_BEGIN}登录 Web」续登。")
            try:
                delivered, error = await send_private_msg(user_id=recipient, message=message)
            except Exception as exc:
                logger.warning(f"网页凭据提醒发送结果无法确认：{type(exc).__name__}")
                continue
            if not delivered and isinstance(error, (ActionFailed, PrivateMessageUnavailable)):
                await Repository.update(lambda latest: release(latest, recipient))


@scheduler.scheduled_job("interval", minutes=15, id="mystool-web-expiry-reminder")
async def check_web_expiry_reminders():
    await send_estimated_web_expiry_reminders()


@get_driver().on_bot_connect
async def _check_web_expiry_on_connect():
    await send_estimated_web_expiry_reminders()
