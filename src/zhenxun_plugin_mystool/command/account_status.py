import asyncio

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Message, MessageEvent, PrivateMessageEvent
from nonebot.internal.params import ArgStr
from nonebot.matcher import Matcher
from nonebot.params import T_State
from nonebot.rule import Rule

from ..api import BaseGameSign
from ..api.common import get_bound_game_roles, get_user_myb
from ..model import CommandUsage, UserAccount, plugin_config
from ..services.credential_expiry import credential_fingerprint, web_token_expiry_notice
from ..services.game_roles import role_key, role_label
from ..storage.repository import Repository
from ..utils import COMMAND_BEGIN
from .account_selection import AccountMenu, InvalidAccountChoice, StaleAccountChoice
from .common import CommandRegistry

__all__ = ["account_status", "sign_roles"]

ACCOUNT_PROMPT = "请选择米游社账号。"
CANCEL = {"取消", "退出"}


async def _is_private(event: MessageEvent) -> bool:
    return isinstance(event, PrivateMessageEvent)


prefix = plugin_config.preference.command_start
account_status = on_command(
    prefix + "账号状态", aliases={"米游社账号状态"} - {prefix + "账号状态"},
    rule=Rule(_is_private), priority=4, block=True,
)
sign_roles = on_command(prefix + "签到角色", rule=Rule(_is_private), priority=4, block=True)
CommandRegistry.set_usage(account_status, CommandUsage(name="账号状态", description="私聊查询 App 和网页凭据状态。"))
CommandRegistry.set_usage(sign_roles, CommandUsage(name="签到角色", description="私聊选择每个游戏要自动签到的角色。"))


@account_status.handle()
@sign_roles.handle()
async def _choose_account(event: PrivateMessageEvent, matcher: Matcher, state: T_State):
    menu = AccountMenu.from_records(await Repository.load(), event.get_user_id())
    if not menu:
        await matcher.finish(f"尚未绑定米游社账号，请私聊发送「{COMMAND_BEGIN}登录」。")
    state["account_menu"] = menu
    if len(menu) == 1:
        matcher.set_arg("account_choice", Message("1"))
    else:
        await matcher.send(menu.prompt(ACCOUNT_PROMPT))


async def _selected_account(event, matcher, state, choice):
    if choice.strip() in CANCEL:
        await matcher.finish("已取消。")
    records = await Repository.load()
    menu = AccountMenu.from_records(records, event.get_user_id())
    try:
        uid = state["account_menu"].pick(choice, menu)
    except StaleAccountChoice:
        await matcher.finish("账号列表已变化，请重新查询。")
    except InvalidAccountChoice:
        await matcher.reject(menu.prompt(ACCOUNT_PROMPT))
    return menu, uid, UserAccount.parse_obj(records["accounts"][(menu.owner, uid)])


def _current_account(records, actor, menu, uid):
    if not menu.has_same_account(uid, AccountMenu.from_records(records, actor)):
        return None
    return UserAccount.parse_obj(records["accounts"][(menu.owner, uid)])


async def _probe(account, family):
    if not account.cookies.has_credentials(family):
        return "未绑定"
    if family == "app":
        status, _ = await get_user_myb(account, retry=False)
    else:
        status, _ = await get_bound_game_roles(account)
    if status:
        return "可用"
    if status.login_expired:
        return "已失效"
    return "暂无法验证"


@account_status.got("account_choice")
async def _show_status(event: PrivateMessageEvent, matcher: Matcher, state: T_State, account_choice=ArgStr()):
    menu, uid, account = await _selected_account(event, matcher, state, account_choice)
    app, web = await asyncio.gather(_probe(account, "app"), _probe(account, "web"))
    current = _current_account(await Repository.load(), event.get_user_id(), menu, uid)
    if current is None or credential_fingerprint(current, "combined") != credential_fingerprint(account, "combined"):
        await matcher.finish("账号或凭据已变化，请重新查询。")
    await matcher.finish(
        f"账户 {menu.label_for(uid)}\nApp 凭据：{app}\n网页凭据：{web}\n"
        f"{web_token_expiry_notice(current.web_scanned_at)}"
    )


@sign_roles.got("account_choice")
async def _list_games(event: PrivateMessageEvent, matcher: Matcher, state: T_State, account_choice=ArgStr()):
    menu, uid, account = await _selected_account(event, matcher, state, account_choice)
    status, records = await get_bound_game_roles(account)
    if not status:
        await matcher.finish("暂无法验证游戏角色。请先通过账号状态检查网页凭据。")
    current = _current_account(await Repository.load(), event.get_user_id(), menu, uid)
    version = credential_fingerprint(account, "web")
    if current is None or credential_fingerprint(current, "web") != version:
        await matcher.finish("账号或网页凭据已变化，请重新执行命令。")
    options = {}
    for game in sorted(BaseGameSign.available_game_signs, key=lambda item: item.game_id):
        roles = [record for record in records if record.game_id == game.game_id]
        if roles:
            options[str(len(options) + 1)] = (game, roles)
    if not options:
        await matcher.finish("没有查询到支持的游戏角色。")
    state.update(role_menu=menu, role_uid=uid, role_version=version, games=options,
                 previous_roles=current.game_sign_roles)
    await matcher.send("请选择游戏，或回复「取消」。\n" + "\n".join(
        f"{number}. {game.name}" for number, (game, _) in options.items()
    ))


@sign_roles.got("game_choice")
async def _list_roles(event: PrivateMessageEvent, matcher: Matcher, state: T_State, game_choice=ArgStr()):
    choice = game_choice.strip()
    if choice in CANCEL:
        await matcher.finish("已取消。")
    if choice not in state["games"]:
        await matcher.reject("请回复游戏序号，或回复「取消」。")
    game, records = state["games"][choice]
    roles = {str(index): record for index, record in enumerate(records, start=1)}
    state.update(role_game=game, roles=roles)
    await matcher.send(f"{game.name}签到角色\n" + "\n".join(
        f"{number}. {role_label(record)}" for number, record in roles.items()
    ) + "\n请回复角色序号，多选用空格分隔。回复 0 停止该游戏的角色签到。")


@sign_roles.got("role_choice")
async def _store_roles(event: PrivateMessageEvent, matcher: Matcher, state: T_State, role_choice=ArgStr()):
    selected = role_choice.strip()
    if selected in CANCEL:
        await matcher.finish("已取消。")
    options = state["roles"]
    choices = [] if selected == "0" else list(dict.fromkeys(selected.split()))
    if selected != "0" and (not choices or any(number not in options for number in choices)):
        await matcher.reject("请回复有效的角色序号，多选用空格分隔，或回复「取消」。")
    game, menu, uid = state["role_game"], state["role_menu"], state["role_uid"]
    version, actor = state["role_version"], event.get_user_id()
    keys = [role_key(options[number]) for number in choices]
    current = _current_account(await Repository.load(), actor, menu, uid)
    if current is None or credential_fingerprint(current, "web") != version:
        await matcher.finish("账号或网页凭据已变化，请重新选择。")
    if keys:
        status, records = await get_bound_game_roles(current)
        if not status:
            await matcher.finish("暂无法重新核对绑定角色，请稍后重试。")
        live = {role_key(record) for record in records if record.game_id == game.game_id}
        if not set(keys) <= live:
            await matcher.finish("角色绑定已变化，请重新选择。")

    def save(records):
        current = _current_account(records, actor, menu, uid)
        if current is None or credential_fingerprint(current, "web") != version:
            return False
        if current.game_sign_roles.get(game.en_name) != state["previous_roles"].get(game.en_name):
            return False
        raw = records["accounts"][(menu.owner, uid)]
        raw.setdefault("game_sign_roles", {})[game.en_name] = [list(key) for key in keys]
        return True

    if not await Repository.update(save):
        await matcher.finish("账号绑定或角色设置已变化，请重新选择。")
    await matcher.finish(f"已保存{game.name}的签到角色，共 {len(keys)} 个。自动便笺也按已选角色检查。")
