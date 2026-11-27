import asyncio
import json
from dataclasses import dataclass
from time import time
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
from ..api.profile import fetch_community_nickname
from ..command.account_selection import (
    AccountMenu, InvalidAccountChoice, StaleAccountChoice, ask_for_account, select_account,
)
from ..command.common import CommandRegistry
from ..model import BBSCookies, plugin_config, plugin_env, CommandUsage
from ..services.account_profiles import refresh_account_profiles
from ..services.credential_expiry import web_token_expiry_notice
from ..services.login import LoginConflict, login_cookies, select_login, write_login
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
        description="私聊扫码绑定米游社账号，或按序号选择账号续登。"
        "「登录 App」和「登录 Web」可单独刷新指定凭据。发送「登录 取消」结束扫码。",
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

    refresh = choice.lower() if choice.lower() in {"app", "web"} else None
    if choice not in {"", "新增"} and refresh is None:
        await matcher.finish(f"请私聊发送「{COMMAND_BEGIN}登录」，再按列表回复序号。")
    records = await Repository.load()
    owner = records["bindings"].get(actor, actor)
    if has_active_login(owner, actor):
        await matcher.finish("正在等待扫码确认。发起者可以发送登录命令加「取消」结束。")

    if choice != "新增":
        records = await refresh_account_profiles(records, actor)
    menu = AccountMenu.from_records(records, actor)
    matcher.state["account_menu"] = menu
    matcher.state["login_refresh"] = refresh
    if refresh and not menu:
        await matcher.finish(f"当前没有可续登的账号，请先私聊发送「{COMMAND_BEGIN}登录」。")
    if choice == "新增" or not menu:
        matcher.set_arg("login_target", Message("0"))
        return
    await matcher.send(menu.prompt(login_menu_title(refresh), allow_new=refresh is None))


def login_menu_title(refresh):
    if refresh == "app":
        return "请选择要刷新 App 凭据的账号"
    if refresh == "web":
        return "请选择要刷新网页凭据的账号"
    return "请选择要续登的账号"


@get_cookie.got("login_target")
async def login_account(event: PrivateMessageEvent, matcher: Matcher, login_target: str = ArgStr()):
    choice = login_target.strip()
    if choice in {"取消", "退出"}:
        await matcher.finish("已取消登录。")
    actor = event.get_user_id()
    refresh = matcher.state.get("login_refresh")
    records = await Repository.load()
    current = AccountMenu.from_records(records, actor)
    menu = matcher.state.get("account_menu")
    if refresh and not current:
        await matcher.finish(f"当前没有可续登的账号，请先私聊发送「{COMMAND_BEGIN}登录」。")
    try:
        if menu is None:
            raise StaleAccountChoice
        uid = menu.pick(choice, current, allow_new=refresh is None)
    except StaleAccountChoice:
        matcher.state["account_menu"] = current
        await matcher.reject(current.prompt(
            f"账号列表已变化。{login_menu_title(refresh)}", allow_new=refresh is None,
        ))
    except InvalidAccountChoice:
        await matcher.reject(menu.prompt(login_menu_title(refresh), allow_new=refresh is None))

    try:
        target = select_login(records, actor, uid, refresh=refresh)
    except LoginConflict as exc:
        await matcher.finish(str(exc))

    if has_active_login(target.owner, target.actor):
        await matcher.finish("正在等待扫码确认，请完成当前登录后再试。")

    login = ActiveLogin(target.actor, asyncio.create_task(collect_credentials(matcher, target)))
    _active_logins[target.owner] = login
    try:
        app, web, device_fp, web_scanned_at = await login.scan
        uid = target.uid or app.uid
        old_account = records["accounts"].get((target.owner, uid), {})
        cookies = BBSCookies.parse_obj(login_cookies(uid, old_account.get("cookies") or {}, app, web))
        device_id = old_account.get("device_id_android") or target.device_id
        try:
            nickname = await fetch_community_nickname(uid, device_id, cookies)
        except Exception as exc:
            logger.warning(f"米游社社区昵称查询失败：{type(exc).__name__}")
            nickname = None

        def save_login(current_records):
            saved_uid = write_login(
                current_records, target, app, web, target.device_id, device_fp,
                max_users=plugin_config.preference.max_user, community_nickname=nickname,
                web_scanned_at=web_scanned_at,
            )
            account = current_records["accounts"][(target.owner, saved_uid)]
            return AccountMenu.from_records(current_records, actor).label_for(saved_uid), account.get("web_scanned_at")

        display_name, saved_web_scan = await Repository.update(save_login)
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
    expiry_tip = web_token_expiry_notice(saved_web_scan, just_scanned=web_scanned_at is not None)
    await matcher.finish(f"米游社账号「{display_name}」{result}。\n{expiry_tip}")


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
        app = web = device_fp = web_scanned_at = None
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
            web_scanned_at = int(time())
        if app:
            device = await get_device_fp(target.device_id, retry=False)
            if device:
                _, device_fp = device
        return app, web, device_fp, web_scanned_at


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
async def prepare_export(event: Union[GeneralMessageEvent], matcher: Matcher, state: T_State):
    if isinstance(event, GeneralGroupMessageEvent):
        await matcher.finish("⚠️为了保护您的隐私，请私聊进行 Cookies 导出。")
    await ask_for_account(matcher, state, event, "请选择要导出 Cookies 的账号")


@output_cookies.got("account_choice")
@data_session
async def export_account_cookies(
    event: Union[GeneralPrivateMessageEvent], matcher: Matcher, state: T_State, account_choice=ArgStr(),
):
    account = await select_account(matcher, state, event, account_choice, "请选择要导出 Cookies 的账号")
    cookies = account.cookies.dict(v2_stoken=True, cookie_type=True)
    await matcher.finish(json.dumps(cookies, indent=4))
