from typing import Union

from nonebot import on_command
from nonebot.internal.params import ArgStr
from nonebot.matcher import Matcher
from nonebot.params import T_State

from ..api import BaseMission, BaseGameSign
from ..command.common import CommandRegistry
from ..model import PluginDataManager, plugin_config, UserAccount, CommandUsage
from ..utils import COMMAND_BEGIN, GeneralMessageEvent

__all__ = ["setting", "account_setting", "global_setting"]

setting = on_command(plugin_config.preference.command_start + '设置', priority=4, block=True)

CommandRegistry.set_usage(
    setting,
    CommandUsage(
        name="设置",
        description="如需配置是否开启每日任务、设备平台、频道任务等相关选项，请使用『{HEAD}账号设置』命令。\n"
                    "如需设置米游币任务和游戏签到后是否进行QQ通知，请使用『{HEAD}通知设置』命令。"
    )
)


@setting.handle()
async def _(_: Union[GeneralMessageEvent]):
    msg = f'如需配置是否开启每日任务、设备平台、频道任务等相关选项，请使用『{COMMAND_BEGIN}账号设置』命令' \
          f'\n如需设置米游币任务和游戏签到后是否进行QQ通知，请使用『{COMMAND_BEGIN}通知设置』命令'
    await setting.send(msg)


account_setting = on_command(plugin_config.preference.command_start + '账号设置', priority=5, block=True)

CommandRegistry.set_usage(
    account_setting,
    CommandUsage(
        name="账号设置",
        description="配置游戏自动签到、米游币任务是否开启、设备平台、频道任务相关选项"
    )
)


@account_setting.handle()
async def _(event: Union[GeneralMessageEvent], matcher: Matcher, state: T_State):
    """
    账号设置命令触发
    """
    user = PluginDataManager.plugin_data.users.get(event.get_user_id())
    user_account = user.accounts if user else None
    if not user_account:
        await account_setting.finish(
            f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录")
    if len(user_account) == 1:
        uid = next(iter(user_account.values())).bbs_uid
        state["bbs_uid"] = uid
    else:
        msg = "您有多个账号，您要更改以下哪个账号的设置？\n"
        msg += "\n".join(map(lambda x: f"🆔{x.display_name}", user_account.values()))
        msg += "\n🚪发送“退出”即可退出"
        await matcher.send(msg)


@account_setting.got('bbs_uid')
async def _(event: Union[GeneralMessageEvent], matcher: Matcher, state: T_State, bbs_uid=ArgStr()):
    """
    根据手机号设置相应的账户
    """
    if bbs_uid == '退出':
        await matcher.finish('🚪已成功退出')

    user_account = PluginDataManager.plugin_data.users[event.get_user_id()].accounts
    if not (account := user_account.get(bbs_uid)):
        await account_setting.reject('⚠️您发送的账号不在以上账号内，请重新发送')
    state["user"] = PluginDataManager.plugin_data.users[event.get_user_id()]
    state['account'] = account
    state["prepare_to_delete"] = False

    user_setting = ""
    user_setting += f"1️⃣ 米游币任务自动执行：{'开' if account.enable_mission else '关'}"
    user_setting += f"\n2️⃣ 游戏自动签到：{'开' if account.enable_game_sign else '关'}"

    # 筛选出用户数据中的missionGame对应的游戏全称
    user_setting += "\n\n3️⃣ 执行签到的游戏：" + \
                    "\n- " + "、".join(
        f"『{next((game.name for game in BaseGameSign.available_game_signs if game.en_name == game_id), 'N/A')}』"
        for game_id in account.game_sign_games
    )

    platform_show = "iOS" if account.platform == "ios" else "安卓"
    user_setting += f"\n4️⃣ 设备平台：{platform_show}"

    # 筛选出用户数据中的missionGame对应的游戏全称
    user_setting += "\n\n5️⃣ 执行米游币任务的频道：" + \
                    "\n- " + "、".join(
        map(
            lambda x: f"『{x.name}』" if x else "『N/A』",
            map(
                BaseMission.available_games.get,
                account.mission_games
            )
        )
    )

    user_setting += f"\n\n6️⃣ 实时便笺体力提醒：{'开' if account.enable_resin else '关'}"
    user_setting += f"\n7️⃣更改便笺体力提醒阈值 \
                      \n   当前原神提醒阈值：{account.user_resin_threshold} \
                      \n   当前崩铁提醒阈值：{account.user_stamina_threshold}"
    user_setting += "\n8️⃣⚠️删除账户数据"

    await account_setting.send(user_setting + '\n\n您要更改哪一项呢？请发送 1 / 2 / 3 / 4 / 5 / 6 / 7/ 8'
                                              '\n🚪发送“退出”即可退出')


@account_setting.got('setting_id')
async def _(event: Union[GeneralMessageEvent], state: T_State, setting_id=ArgStr()):
    """
    根据所选更改相应账户的相应设置
    """
    account: UserAccount = state['account']
    user_account = PluginDataManager.plugin_data.users[event.get_user_id()].accounts
    if setting_id == '退出':
        await account_setting.finish('🚪已成功退出')
    elif setting_id == '1':
        account.enable_mission = not account.enable_mission
        PluginDataManager.write_plugin_data()
        await account_setting.finish(f"📅米游币任务自动执行已 {'✅开启' if account.enable_mission else '❌关闭'}")
    elif setting_id == '2':
        account.enable_game_sign = not account.enable_game_sign
        PluginDataManager.write_plugin_data()
        await account_setting.finish(f"📅米哈游游戏自动签到已 {'✅开启' if account.enable_game_sign else '❌关闭'}")
    elif setting_id == '3':
        signable_games = "、".join(f"『{game.name}』" for game in BaseGameSign.available_game_signs)
        await account_setting.send(
            "请发送你想要执行签到的游戏："
            "\n❕多个游戏请用空格分隔，如 “原神 崩坏3 综合”"
            "\n\n可选的游戏："
            f"\n- {signable_games}"
            "\n\n🚪发送“退出”即可退出"
        )
        state["setting_item"] = "sign_games"
    elif setting_id == '4':
        if account.platform == "ios":
            account.platform = "android"
            platform_show = "安卓"
        else:
            account.platform = "ios"
            platform_show = "iOS"
        PluginDataManager.write_plugin_data()
        await account_setting.finish(f"📲设备平台已更改为 {platform_show}")
    elif setting_id == '5':
        games_show = "、".join(map(lambda x: f"『{x.name}』", BaseMission.available_games.values()))
        await account_setting.send(
            "请发送你想要执行米游币任务的频道："
            "\n❕多个频道请用空格分隔，如 “原神 崩坏3 综合”"
            "\n\n可选的游戏："
            f"\n- {games_show}"
            "\n\n🚪发送“退出”即可退出"
        )
        state["setting_item"] = "mission_games"
    elif setting_id == '6':
        account.enable_resin = not account.enable_resin
        PluginDataManager.write_plugin_data()
        await account_setting.finish(f"📅原神、星穹铁道便笺提醒已 {'✅开启' if account.enable_resin else '❌关闭'}")
    elif setting_id == '7':
        await account_setting.send(
            "请发送想要修改体力提醒阈值的游戏编号："
            "\n1. 原神"
            "\n2. 崩坏：星穹铁道"
            "\n\n🚪发送“退出”即可退出"
        )
        state["setting_item"] = "setting_notice_value"
        return
    elif setting_id == '8':
        state["prepare_to_delete"] = True
        await account_setting.reject(f"⚠️确认删除账号 {account.display_name} ？发送 \"确认删除\" 以确定。")
    elif setting_id == '确认删除' and state["prepare_to_delete"]:
        user_account.pop(account.bbs_uid)
        PluginDataManager.write_plugin_data()
        await account_setting.finish(f"已删除账号 {account.display_name} 的数据")
    else:
        await account_setting.reject("⚠️您的输入有误，请重新输入")
    state["notice_game"] = ""


@account_setting.got('notice_game')
async def _(_: Union[GeneralMessageEvent], state: T_State, notice_game=ArgStr()):
    if notice_game == '退出':
        await account_setting.finish('🚪已成功退出')
    elif state["setting_item"] == "setting_notice_value":
        if notice_game == "1":
            await account_setting.send(
                "请输入想要所需通知阈值，树脂达到该值时将进行通知："
                "可用范围 [0, 200]"
                "\n\n🚪发送“退出”即可退出"
            )
            state["setting_item"] = "setting_notice_value_op"
        elif notice_game == "2":
            await account_setting.send(
                "请输入想要所需阈值数字，开拓力达到该值时将进行通知："
                "可用范围 [0, 240]"
                "\n\n🚪发送“退出”即可退出"
            )
            state["setting_item"] = "setting_notice_value_sr"
        else:
            await account_setting.reject("⚠️您的输入有误，请重新输入")


@account_setting.got('setting_value')
async def _(_: Union[GeneralMessageEvent], state: T_State, setting_value=ArgStr()):
    if setting_value == '退出':
        await account_setting.finish('🚪已成功退出')
    account: UserAccount = state['account']

    if state["setting_item"] == "setting_notice_value_op":
        try:
            resin_threshold = int(setting_value)
        except ValueError:
            await account_setting.reject("⚠️请输入有效的数字。")
        else:
            if 0 <= resin_threshold <= 200:
                # 输入有效的数字范围，将 resin_threshold 赋值为输入的整数
                account.user_resin_threshold = resin_threshold
                PluginDataManager.write_plugin_data()
                await account_setting.finish("更改原神便笺树脂提醒阈值成功\n"
                                             f"⏰当前提醒阈值：{resin_threshold}")
            else:
                await account_setting.reject("⚠️输入的数字范围应在 0 到 200 之间。")

    elif state["setting_item"] == "setting_notice_value_sr":
        try:
            stamina_threshold = int(setting_value)
        except ValueError:
            await account_setting.reject("⚠️请输入有效的数字。")
        else:
            if 0 <= stamina_threshold <= 240:
                # 输入有效的数字范围，将 stamina_threshold 赋值为输入的整数
                account.user_stamina_threshold = stamina_threshold
                PluginDataManager.write_plugin_data()
                await account_setting.finish("更改崩铁便笺开拓力提醒阈值成功\n"
                                             f"⏰当前提醒阈值：{stamina_threshold}")
            else:
                await account_setting.reject("⚠️输入的数字范围应在 0 到 240 之间。")

    elif state["setting_item"] == "sign_games":
        games_input = setting_value.split()
        sign_games = []
        for game in games_input:
            subclass_filter = filter(lambda x: x.name == game, BaseGameSign.available_game_signs)
            subclass_pair = next(subclass_filter, None)
            if subclass_pair is None:
                await account_setting.reject("⚠️您的输入有误，请重新输入")
            else:
                game_name = subclass_pair.en_name
                sign_games.append(game_name)

        account.game_sign_games = sign_games
        PluginDataManager.write_plugin_data()
        setting_value = setting_value.replace(" ", "、")
        await account_setting.finish(f"💬执行签到的游戏已更改为『{setting_value}』")

    elif state["setting_item"] == "mission_games":
        games_input = setting_value.split()
        mission_games = []
        for game in games_input:
            subclass_filter = filter(lambda x: x[1].name == game, BaseMission.available_games.items())
            subclass_pair = next(subclass_filter, None)
            if subclass_pair is None:
                await account_setting.reject("⚠️您的输入有误，请重新输入")
            else:
                game_name, _ = subclass_pair
                mission_games.append(game_name)

        account.mission_games = mission_games
        PluginDataManager.write_plugin_data()
        setting_value = setting_value.replace(" ", "、")
        await account_setting.finish(f"💬执行米游币任务的频道已更改为『{setting_value}』")


global_setting = on_command(plugin_config.preference.command_start + '通知设置', priority=5, block=True)

CommandRegistry.set_usage(
    global_setting,
    CommandUsage(
        name="通知设置",
        description="设置每日签到后是否进行QQ通知"
    )
)


@global_setting.handle()
async def _(event: Union[GeneralMessageEvent], matcher: Matcher):
    """
    通知设置命令触发
    """
    user = PluginDataManager.plugin_data.users[event.get_user_id()]
    await matcher.send(
        f"自动通知每日计划任务结果：{'🔔开' if user.enable_notice else '🔕关'}"
        "\n请问您是否需要更改呢？\n请回复“是”或“否”\n🚪发送“退出”即可退出")


@global_setting.got('choice')
async def _(event: Union[GeneralMessageEvent], matcher: Matcher, choice=ArgStr()):
    """
    根据选择变更通知设置
    """
    user = PluginDataManager.plugin_data.users[event.get_user_id()]
    if choice == '退出':
        await matcher.finish("🚪已成功退出")
    elif choice == '是':
        user.enable_notice = not user.enable_notice
        PluginDataManager.write_plugin_data()
        await matcher.finish(f"自动通知每日计划任务结果 已 {'🔔开启' if user.enable_notice else '🔕关闭'}")
    elif choice == '否':
        await matcher.finish("没有做修改哦~")
    else:
        await matcher.reject("⚠️您的输入有误，请重新输入")
