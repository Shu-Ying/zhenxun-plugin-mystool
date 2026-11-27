import asyncio
from ..storage import data_session
from typing import Union

from nonebot import on_command
from nonebot.internal.params import ArgStr
from nonebot.matcher import Matcher
from nonebot.params import T_State

from ..api.common import get_address
from .account_selection import ask_for_account, require_selected_account, save_selected_account, select_account
from .common import CommandRegistry
from ..model import CommandUsage, plugin_config
from ..utils import GeneralMessageEvent, GeneralPrivateMessageEvent, \
    GeneralGroupMessageEvent

__all__ = [
    "address_matcher"
]

address_matcher = on_command(plugin_config.preference.command_start + '地址', priority=4, block=True)
ACCOUNT_PROMPT = "请选择要设置收货地址的米游社账号。"

CommandRegistry.set_usage(
    address_matcher,
    CommandUsage(
        name="地址",
        description="跟随指引，获取地址ID，用于兑换米游币商品。在获取地址ID前，如果你还没有设置米游社收获地址，请前往官网或App设置"
    )
)


@address_matcher.handle()
@data_session
async def _(event: Union[GeneralMessageEvent], matcher: Matcher, state: T_State):
    if isinstance(event, GeneralGroupMessageEvent):
        await address_matcher.finish("⚠️为了保护您的隐私，请私聊进行地址设置。")
    await ask_for_account(matcher, state, event, ACCOUNT_PROMPT)


@address_matcher.got('account_choice')
@data_session
async def _(event: Union[GeneralPrivateMessageEvent], matcher: Matcher, state: T_State, account_choice=ArgStr()):
    account = await select_account(matcher, state, event, account_choice, ACCOUNT_PROMPT)

    address_status, address_list = await get_address(account)
    state['address_list'] = address_list
    if not address_status:
        if address_status.login_expired:
            label = state["account_menu"].label_for(account.bbs_uid)
            await address_matcher.finish(f"⚠️账户 {label} 登录失效，请重新登录")
        await address_matcher.finish("⚠️获取失败，请稍后重新尝试")

    if address_list:
        address_text = map(
            lambda x: f"省 ➢ {x.province_name}\n"
                      f"市 ➢ {x.city_name}\n"
                      f"区/县 ➢ {x.county_name}\n"
                      f"详细地址 ➢ {x.addr_ext}\n"
                      f"联系电话 ➢ {x.phone}\n"
                      f"联系人 ➢ {x.connect_name}\n"
                      f"地址ID ➢ {x.id}",
            address_list
        )
        msg = "以下为查询结果：" \
              "\n\n" + "\n- - -\n".join(address_text)
        await address_matcher.send(msg)
        await asyncio.sleep(0.2)
    else:
        await address_matcher.finish("⚠️您还没有配置地址，请先前往米游社配置地址！")


@address_matcher.got('address_id', prompt='请发送你要选择的地址ID，或发送“退出”。')
@data_session
async def _(event: Union[GeneralPrivateMessageEvent], matcher: Matcher, state: T_State, address_id=ArgStr()):
    if address_id == "退出":
        await address_matcher.finish("🚪已成功退出")

    account = await require_selected_account(matcher, state, event)
    address_filter = filter(lambda x: x.id == address_id, state['address_list'])
    address = next(address_filter, None)
    if address is not None:
        account.address = address
        await save_selected_account(matcher, state, event)
        label = state["account_menu"].label_for(account.bbs_uid)
        await address_matcher.finish(f"🎉已成功设置账户 {label} 的地址")
    else:
        await address_matcher.reject("⚠️您发送的地址ID与查询结果不匹配，请重新发送")
