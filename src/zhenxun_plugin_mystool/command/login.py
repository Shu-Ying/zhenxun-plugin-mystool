import asyncio
import json
from dataclasses import dataclass
from typing import Union

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.exception import ActionFailed
from nonebot.internal.matcher import Matcher
from nonebot.internal.params import ArgStr
from nonebot.params import CommandArg, T_State
from tortoise.exceptions import BaseORMException

from ..api.common import get_device_fp
from ..api.passport import PassportClient, PassportError, PassportRefused, QrExpired
from ..command.common import CommandRegistry
from ..model import PluginDataManager, plugin_config, plugin_env, CommandUsage
from ..services.login import LoginConflict, select_login, write_login
from ..storage import data_session
from ..storage.repository import ConcurrentUpdateError, Repository
from ..utils import (
    COMMAND_BEGIN, GeneralGroupMessageEvent, GeneralMessageEvent, GeneralPrivateMessageEvent,
    generate_qr_img, logger,
)

__all__ = ["get_cookie", "output_cookies"]

get_cookie = on_command(plugin_config.preference.command_start + "登录", priority=4, block=True)


@dataclass
class ActiveLogin:
    actor: str
    scan: asyncio.Task
    cancelled: bool = False


_active_logins: dict[str, ActiveLogin] = {}


def has_active_login(owner: str, actor: str) -> bool:
    return owner in _active_logins or any(login.actor == actor for login in _active_logins.values())


CommandRegistry.set_usage(
    get_cookie,
    CommandUsage(
        name="登录",
        description="私聊扫码绑定米游社账号或续登。已有账号可用「登录 UID」续登，"
        "也可用「登录 App UID」或「登录 Web UID」只刷新指定凭据。发送「登录 取消」结束扫码。",
    ),
)


@get_cookie.handle()
async def prepare_login(event: MessageEvent, matcher: Matcher, args: Message = CommandArg()):
    if not isinstance(event, PrivateMessageEvent):
        await matcher.finish("请私聊发送登录命令，二维码只在私聊中发送。")

    actor = event.get_user_id()
    choice = args.extract_plain_text().strip()
    if choice in {"取消", "退出"}:
        for login in _active_logins.values():
            if login.actor == actor and not login.scan.done():
                login.cancelled = True
                login.scan.cancel()
                await matcher.finish()
        await matcher.finish("当前没有可以取消的扫码。")

    records = await Repository.load()
    owner = records["bindings"].get(actor, actor)
    if has_active_login(owner, actor):
        await matcher.finish("正在等待扫码确认。发起者可以发送登录命令加「取消」结束。")

    accounts = sorted(uid for owner_id, uid in records["accounts"] if owner_id == owner)
    if choice or not accounts:
        matcher.set_arg("login_target", Message(choice or "新增"))
        return
    await matcher.send("请选择续登账号，回复米游社 UID 或回复「新增」：\n" + "\n".join(accounts))


@get_cookie.got("login_target")
async def login_account(event: PrivateMessageEvent, matcher: Matcher, login_target: str = ArgStr()):
    choice = login_target.strip()
    if choice in {"取消", "退出"}:
        await matcher.finish("已取消登录。")
    family, _, value = choice.partition(" ")
    refresh = family.casefold() if family.casefold() in {"app", "web"} else None
    uid = value.strip() if refresh else choice
    if (uid != "新增" or refresh) and (not uid.isascii() or not uid.isdecimal()):
        await matcher.reject("请输入米游社 UID、App UID、Web UID、「新增」或「取消」。")

    try:
        target = select_login(
            await Repository.load(), event.get_user_id(),
            None if uid == "新增" else uid, refresh=refresh,
        )
    except LoginConflict as exc:
        await matcher.finish(str(exc))

    if has_active_login(target.owner, target.actor):
        await matcher.finish("正在等待扫码确认，请完成当前登录后再试。")

    login = ActiveLogin(target.actor, asyncio.create_task(collect_credentials(matcher, target)))
    _active_logins[target.owner] = login
    try:
        app, web, device_fp = await login.scan
        uid = await Repository.update(lambda records: write_login(
            records, target, app, web, target.device_id, device_fp,
            max_users=plugin_config.preference.max_user,
        ))
    except asyncio.CancelledError:
        if login.cancelled:
            await matcher.finish("已取消扫码，原账号数据已保留。")
        raise
    except (LoginConflict, ConcurrentUpdateError) as exc:
        await matcher.finish(f"登录未完成：{exc}")
    except PassportError as exc:
        if isinstance(exc, PassportRefused) and exc.endpoint:
            logger.warning(f"米游社扫码 {exc.endpoint} HTTP={exc.status_code} retcode={exc.retcode}")
        else:
            logger.warning(f"米游社扫码未完成：{exc}")
        await matcher.finish(f"登录未完成：{exc}")
    except BaseORMException as exc:
        logger.error(f"米游社登录保存失败：{type(exc).__name__}")
        await matcher.finish("登录凭据保存失败，请稍后重新登录。")
    except ActionFailed:
        await matcher.finish("发送登录二维码失败，请重新发起登录。")
    finally:
        _active_logins.pop(target.owner, None)

    logger.info(f"米游社账号 {uid} 的登录凭据已保存")
    result = "绑定成功" if target.uid is None else "续登成功"
    await matcher.finish(f"米游社账号 {uid} {result}。")


async def send_qr(matcher: Matcher, kind: str, qr):
    await matcher.send(f"请用米游社 App 扫描{kind}二维码，并在手机上确认。")
    await matcher.send(MessageSegment.image(generate_qr_img(qr.url)))


async def collect_credentials(matcher: Matcher, target):
    preference = plugin_config.preference
    async with PassportClient(
        request_timeout=preference.timeout,
        poll_interval=preference.qrcode_query_interval,
        qr_lifetime=preference.qrcode_wait_time,
        app_version=plugin_env.device_config.X_RPC_APP_VERSION,
        retry_attempts=(preference.max_retry_times or 0) + 1,
        retry_interval=preference.retry_interval,
    ) as client:
        app = web = device_fp = None
        if target.need_app:
            qr = await client.create_app_qr(target.device_id)
            await send_qr(matcher, "App 登录", qr)
            app = await client.wait_app_qr(qr)
            if target.uid and app.uid != target.uid:
                raise LoginConflict("App 二维码扫描的是另一个米游社账号。")
        if target.need_web:
            qr = await client.create_web_qr(target.device_id)
            await send_qr(matcher, "网页登录", qr)
            try:
                web = await client.wait_web_qr(qr)
            except QrExpired:
                if app is None:
                    raise
                await matcher.send("网页二维码已过期，正在生成新的网页二维码。")
                qr = await client.create_web_qr(target.device_id)
                await send_qr(matcher, "网页登录", qr)
                web = await client.wait_web_qr(qr)
            if (target.uid or app.uid) != web.uid:
                raise LoginConflict("网页二维码扫描的是另一个米游社账号。")
        if app:
            device = await get_device_fp(target.device_id, retry=False)
            if device:
                _, device_fp = device
        return app, web, device_fp


output_cookies = on_command(
    plugin_config.preference.command_start + '导出Cookies',
    aliases={plugin_config.preference.command_start + '导出Cookie', plugin_config.preference.command_start + '导出账号',
             plugin_config.preference.command_start + '导出cookie',
             plugin_config.preference.command_start + '导出cookies'}, priority=4,
    block=True)

CommandRegistry.set_usage(
    output_cookies,
    CommandUsage(
        name="导出Cookies",
        description="导出绑定的米游社账号的Cookies数据"
    )
)


@output_cookies.handle()
@data_session
async def handle_first_receive(event: Union[GeneralMessageEvent], state: T_State):
    """
    Cookies导出命令触发
    """
    if isinstance(event, GeneralGroupMessageEvent):
        await output_cookies.finish("⚠️为了保护您的隐私，请私聊进行Cookies导出。")
    user = PluginDataManager.plugin_data.users.get(event.get_user_id())
    user_account = user.accounts if user else {}
    if not user_account:
        await output_cookies.finish(f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录")
    elif len(user_account) == 1:
        account = next(iter(user_account.values()))
        state["bbs_uid"] = account.bbs_uid
    else:
        msg = "您有多个账号，您要导出哪个账号的Cookies数据？\n"
        msg += "\n".join(map(lambda x: f"🆔{x}", user_account))
        msg += "\n🚪发送“退出”即可退出"
        await output_cookies.send(msg)


@output_cookies.got('bbs_uid')
@data_session
async def _(event: Union[GeneralPrivateMessageEvent], matcher: Matcher, bbs_uid=ArgStr()):
    """
    根据手机号设置导出相应的账户的Cookies
    """
    if bbs_uid == '退出':
        await matcher.finish('🚪已成功退出')
    user = PluginDataManager.plugin_data.users.get(event.get_user_id())
    user_account = user.accounts if user else {}
    if bbs_uid in user_account:
        cookies = user_account[bbs_uid].cookies.dict(v2_stoken=True, cookie_type=True)
        await matcher.finish(json.dumps(cookies, indent=4))
    else:
        await matcher.reject('⚠️您输入的账号不在以上账号内，请重新输入')
