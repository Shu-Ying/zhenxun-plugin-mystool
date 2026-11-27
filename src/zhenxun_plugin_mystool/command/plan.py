import asyncio
from ..storage import data_session
import threading
from typing import Union, Optional, Iterable, Dict

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageSegment as OneBotV11MessageSegment, GroupMessageEvent as OneBotV11GroupMessageEvent, Bot
from nonebot.internal.matcher import Matcher
from nonebot.params import CommandArg
from nonebot_plugin_apscheduler import scheduler
from zhenxun.utils.message import MessageUtils
from pydantic.v1 import BaseModel

from ..api import BaseGameSign, GenshinImpactSign, StarRailSign
from ..api import BaseMission, get_missions_state
from ..api.common import device_login, device_save, get_bound_game_roles, get_role_notes
from ..command.common import CommandRegistry
from ..command.exchange import generate_image
from ..model import (MissionStatus, PluginDataManager, plugin_config, UserData, CommandUsage, GenshinNoteNotice,
                     StarRailNoteNotice)
from ..utils import get_file, logger, COMMAND_BEGIN, GeneralMessageEvent, send_private_msg, get_all_bind, \
    get_unique_users, get_validate
from ..services.credential_expiry import CredentialExpiry, credential_fingerprint, web_token_expiry_notice
from ..services.permissions import can_manage_accounts
from ..services.game_roles import role_key, role_label, select_game_roles

__all__ = [
    "manually_game_sign", "manually_bbs_sign", "manually_genshin_note_check",
    "manually_starrail_note_check"
]

manually_game_sign = on_command(plugin_config.preference.command_start + '签到', priority=5, block=True)

CommandRegistry.set_usage(
    manually_game_sign,
    CommandUsage(
        name="签到",
        description="手动进行游戏签到，查看本次签到奖励及本月签到天数"
    )
)


def _data_owner(user_id: str) -> str:
    return PluginDataManager.plugin_data.user_bind.get(user_id, user_id)


@manually_game_sign.handle()
@data_session
async def _(bot: Bot, event: Union[GeneralMessageEvent], matcher: Matcher, command_arg=CommandArg()):
    """
    手动游戏签到函数
    """
    user_id = event.get_user_id()
    user = PluginDataManager.plugin_data.users.get(user_id)
    if not user or not user.accounts:
        await manually_game_sign.finish(f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录", at_sender=True)
    if command_arg:
        if (specified_user_id := str(command_arg)) == "*" or specified_user_id.isdigit():
            if not await can_manage_accounts(bot, event):
                await manually_game_sign.finish("⚠️代执行需要真寻超级用户或全局管理员权限")
            else:
                if specified_user_id == "*":
                    await manually_game_sign.send("⏳开始为所有用户执行游戏签到...")
                    for user_id_, user_ in get_unique_users():
                        await manually_game_sign.send(f"⏳开始为用户 {user_id_} 执行游戏签到...")
                        await perform_game_sign(
                            bot=bot,
                            user=user_,
                            user_ids=[],
                            matcher=matcher,
                            event=event,
                            owner_id=user_id_,
                        )
                else:
                    specified_user = PluginDataManager.plugin_data.users.get(specified_user_id)
                    if not specified_user:
                        await manually_game_sign.finish(f"⚠️未找到用户 {specified_user_id}", at_sender=True)
                    await manually_game_sign.send(f"⏳开始为用户 {specified_user_id} 执行游戏签到...")
                    await perform_game_sign(
                        bot=bot,
                        user=specified_user,
                        user_ids=[],
                        matcher=matcher,
                        event=event,
                        owner_id=_data_owner(specified_user_id),
                    )
    else:
        await manually_game_sign.send("⏳开始游戏签到...")
        await perform_game_sign(bot=bot, user=user, user_ids=[user_id], matcher=matcher, event=event,
                                owner_id=_data_owner(user_id))


manually_bbs_sign = on_command(plugin_config.preference.command_start + '任务', priority=5, block=True)

CommandRegistry.set_usage(
    manually_bbs_sign,
    CommandUsage(
        name="任务",
        description="手动执行米游币每日任务，可以查看米游币任务完成情况"
    )
)


@manually_bbs_sign.handle()
@data_session
async def _(bot: Bot, event: Union[GeneralMessageEvent], matcher: Matcher, command_arg=CommandArg()):
    """
    手动米游币任务函数
    """
    user_id = event.get_user_id()
    user = PluginDataManager.plugin_data.users.get(user_id)
    if not user or not user.accounts:
        await manually_bbs_sign.finish(f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录", at_sender=True)
    if command_arg:
        if (specified_user_id := str(command_arg)) == "*" or specified_user_id.isdigit():
            if not await can_manage_accounts(bot, event):
                await manually_bbs_sign.finish("⚠️代执行需要真寻超级用户或全局管理员权限")
            else:
                if specified_user_id == "*":
                    await manually_bbs_sign.send("⏳开始为所有用户执行米游币任务...")
                    for user_id_, user_ in get_unique_users():
                        await manually_bbs_sign.send(f"⏳开始为用户 {user_id_} 执行米游币任务...")
                        await perform_bbs_sign(
                            bot=bot,
                            user=user_,
                            user_ids=[],
                            matcher=matcher,
                            event=event,
                            owner_id=user_id_,
                        )
                else:
                    specified_user = PluginDataManager.plugin_data.users.get(specified_user_id)
                    if not specified_user:
                        await manually_bbs_sign.finish(f"⚠️未找到用户 {specified_user_id}")
                    await manually_bbs_sign.send(f"⏳开始为用户 {specified_user_id} 执行米游币任务...")
                    await perform_bbs_sign(
                        bot=bot,
                        user=specified_user,
                        user_ids=[],
                        matcher=matcher,
                        event=event,
                        owner_id=_data_owner(specified_user_id),
                    )
    else:
        await manually_bbs_sign.send("⏳开始执行米游币任务...")
        await perform_bbs_sign(bot=bot, user=user, user_ids=[user_id], matcher=matcher, event=event,
                               owner_id=_data_owner(user_id))


class NoteNoticeStatus(BaseModel):
    """
    账号便笺通知状态
    """
    genshin = GenshinNoteNotice()
    starrail = StarRailNoteNotice()


note_notice_status: Dict[tuple[str, str, int, str, str], NoteNoticeStatus] = {}
"""记录账号对应的便笺通知状态"""

manually_genshin_note_check = on_command(
    plugin_config.preference.command_start + '原神便笺',
    aliases={
        plugin_config.preference.command_start + '便笺',
        plugin_config.preference.command_start + '便签',
        plugin_config.preference.command_start + '原神便签',
    },
    priority=5,
    block=True
)

CommandRegistry.set_usage(
    manually_genshin_note_check,
    CommandUsage(
        name="原神便笺",
        description="手动查看原神实时便笺，即原神树脂、洞天财瓮等信息"
    )
)


@manually_genshin_note_check.handle()
@data_session
async def _(event: Union[GeneralMessageEvent], matcher: Matcher):
    """
    手动查看原神便笺
    """
    user_id = event.get_user_id()
    user = PluginDataManager.plugin_data.users.get(user_id)
    if not user or not user.accounts:
        await manually_game_sign.finish(f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录")
    await genshin_note_check(user=user, user_ids=[user_id], matcher=matcher, owner_id=_data_owner(user_id))


manually_starrail_note_check = on_command(
    plugin_config.preference.command_start + '星穹铁道便笺',
    aliases={
        plugin_config.preference.command_start + '铁道便笺',
        plugin_config.preference.command_start + '铁道便签',
    },
    priority=5,
    block=True
)

CommandRegistry.set_usage(
    manually_starrail_note_check,
    CommandUsage(
        name="星穹铁道便笺",
        description="手动查看星穹铁道实时便笺，即开拓力、每日实训、每周模拟宇宙积分等信息"
    )
)


@manually_starrail_note_check.handle()
@data_session
async def _(event: Union[GeneralMessageEvent], matcher: Matcher):
    """
    手动查看星穹铁道便笺（sr）
    """
    user_id = event.get_user_id()
    user = PluginDataManager.plugin_data.users.get(user_id)
    if not user or not user.accounts:
        await manually_game_sign.finish(f"⚠️你尚未绑定米游社账户，请先使用『{COMMAND_BEGIN}登录』进行登录")
    await starrail_note_check(user=user, user_ids=[user_id], matcher=matcher, owner_id=_data_owner(user_id))


async def perform_game_sign(
        user: UserData,
        user_ids: Iterable[str],
        matcher: Matcher = None,
        bot: Optional[Bot] = None,
        event: Union[GeneralMessageEvent] = None,
        *,
        owner_id: str,
):
    rewards = {}
    for account in user.accounts.values():
        if not matcher and not account.enable_game_sign:
            continue
        expiry = CredentialExpiry(owner_id, account, reply=matcher.send if matcher else None)
        if await expiry.skip("web", "游戏签到"):
            continue
        records_status, records = await get_bound_game_roles(account)
        if await expiry.handle(records_status, "web", "查询签到游戏账号"):
            continue
        messages = []

        async def emit(text, image=None):
            if matcher:
                messages.append(text + OneBotV11MessageSegment.image(image) if image else text)
            elif user.enable_notice:
                message = MessageUtils.build_message([text, image] if image else text)
                for recipient in user_ids:
                    await send_private_msg(user_id=recipient, message=message)

        if not records_status:
            await emit(f"⚠️账户 {account.display_name} 暂无法验证绑定角色，已跳过游戏签到。")
        elif not records:
            await emit(f"⚠️账户 {account.display_name} 未查询到支持的游戏角色。")
        else:
            device_ready = device_expired = web_expired = False

            async def prepare_device():
                nonlocal device_ready, device_expired
                if device_ready:
                    return True
                operation = "安卓签到设备准备"
                if device_expired or await expiry.skip("app", operation):
                    device_expired = True
                    return False
                for request in (device_login, device_save):
                    status = await request(account)
                    if not status:
                        device_expired = await expiry.handle(status, "app", operation)
                        return False
                device_ready = True
                return True

            for game in sorted(BaseGameSign.available_game_signs, key=lambda item: item.game_id):
                if game.en_name not in account.game_sign_games:
                    continue
                selected = select_game_roles(account, game, records)
                if selected is None:
                    await emit(f"⚠️账户 {account.display_name} 的{game.name}需要确认签到角色。"
                               f"请私聊发送「{COMMAND_BEGIN}签到角色」。")
                    continue
                for record in selected:
                    signer = game(account, [record])
                    try:
                        text, image, web_expired = await _sign_role(
                            signer, account, user, expiry, prepare_device, rewards,
                            report=bool(matcher or user.enable_notice),
                        )
                    except Exception:
                        logger.exception(f"{game.name}角色签到异常")
                        text, image = f"⚠️账户 {account.display_name} {game.name} 签到结果暂无法确认。", None
                    if text:
                        await emit(text, image)
                    if web_expired:
                        break
                if web_expired:
                    break
        if messages:
            if isinstance(event, OneBotV11GroupMessageEvent):
                await send_qqGroup(bot, event, messages)
            else:
                for message in messages:
                    await matcher.send(message)


async def _sign_role(signer, account, user, expiry, prepare_device, rewards, *, report):
    prefix = f"🪪账户 {account.display_name}\n🎮{signer.name} · {role_label(signer.record)}"
    status, info = await signer.get_info(account.platform)
    if await expiry.handle(status, "web", f"{signer.name}签到查询"):
        return None, None, True
    previously_signed = info.is_sign if status and info is not None else None
    receipt = None
    if previously_signed is not True:
        receipt, captcha = await signer.sign(account.platform, prepare_device=prepare_device)
        if receipt.need_verify and captcha and (plugin_config.preference.geetest_url or user.geetest_url):
            for _ in range(3):
                validation = await get_validate(user, captcha.gt, captcha.challenge)
                if not validation:
                    continue
                receipt, captcha = await signer.sign(account.platform, captcha, validation, prepare_device=prepare_device)
                if not receipt.need_verify or captcha is None:
                    break
        if await expiry.handle(receipt, "web", f"{signer.name}签到"):
            return None, None, True
        if not report:
            return None, None, False
        await asyncio.sleep(plugin_config.preference.sleep_time)
        final_status, final_info = await signer.get_info(account.platform)
        if await expiry.handle(final_status, "web", f"{signer.name}签到结果查询"):
            return (f"{prefix}\n签到已确认，奖励暂未查到。" if receipt else None), None, True
        info = final_info if final_status else None
    if not report:
        return None, None, False

    confirmed = bool(info and info.is_sign) or bool(receipt)
    if not confirmed:
        if receipt is not None and receipt.need_verify:
            message = f"{prefix}\n签到遇到人机验证，请在米游社确认。"
        elif info is not None:
            message = f"{prefix}\n今日尚未签到，请稍后重试。"
        else:
            message = f"{prefix}\n签到结果暂无法确认。"
        return message, None, False

    if previously_signed is True:
        result = "已经签到过了"
    elif receipt:
        result = "签到成功"
    else:
        result = "今日已签到，无法确认本次新增"
    text = f"{prefix}\n🎮状态：{result}"
    if info is None or not info.is_sign:
        return f"{text}\n🎁奖励暂未查到。", None, False
    days = info.total_sign_day
    text += f"\n📅本月签到次数：{days}"
    try:
        if signer.en_name not in rewards:
            reward_status, awards = await signer.get_rewards()
            if reward_status and awards:
                rewards[signer.en_name] = awards
        awards = rewards.get(signer.en_name, [])
        if not 1 <= days <= len(awards):
            return f"{text}\n🎁奖励暂未查到。", None, False
        award = awards[days - 1]
    except Exception:
        logger.exception(f"{signer.name}奖励列表读取失败")
        return f"{text}\n🎁奖励暂未查到。", None, False
    text += f"\n🎁今日签到奖励：{award.name} × {award.cnt}"
    try:
        image = await get_file(award.icon)
    except Exception:
        logger.exception(f"{signer.name}签到奖励图片下载失败")
        image = None
    return text, image, False


async def perform_bbs_sign(
        user: UserData,
        user_ids: Iterable[str],
        matcher: Matcher = None,
        bot: Optional[Bot] = None,
        event: Union[GeneralMessageEvent] = None,
        *,
        owner_id: str,
):
    for account in user.accounts.values():
        if not matcher and not account.enable_mission:
            continue
        expiry = CredentialExpiry(owner_id, account, reply=matcher.send if matcher else None)
        if await expiry.skip("app", "米游币任务"):
            continue

        status, progress = await get_missions_state(account)
        if await expiry.handle(status, "app", "查询米游币任务进度"):
            continue
        initial_points = progress.current_myb if progress is not None else None
        board_results = []
        action_results = []
        known_boards = set()
        login_expired = False

        for board in account.mission_games:
            mission_class = BaseMission.available_games.get(board)
            if mission_class is None or mission_class.gids in known_boards:
                continue
            known_boards.add(mission_class.gids)
            mission = mission_class(account)
            try:
                signed, points = await mission.sign(user)
            except Exception:
                logger.exception("分区签到异常")
                signed, points = MissionStatus(), None
            mark = "已签到" if signed.already_signed else "✓" if signed else "✕"
            board_results.append(f"📅{mission.name}签到：{mark}" + (f" +{points} 米游币" if points else ""))
            if await expiry.handle(signed, "app", "米游币分区签到"):
                login_expired = True
                break

            if progress is None:
                continue
            actions = (
                (BaseMission.VIEW, "阅读", mission.read, 5),
                (BaseMission.LIKE, "点赞", mission.like, 10),
                (BaseMission.SHARE, "分享", mission.share, 1),
            )
            attempted = False
            results = []
            for key, label, action, limit in actions:
                if key in progress.state_dict:
                    definition, current = progress.state_dict[key]
                elif key in progress.unconfirmed:
                    definition, current = progress.unconfirmed[key], 0
                else:
                    continue
                remaining = min(limit, max(0, definition.threshold - current))
                if not remaining:
                    continue
                attempted = True
                try:
                    action_status = await action() if key == BaseMission.SHARE else await action(remaining)
                except Exception:
                    logger.exception(f"米游币{label}任务异常")
                    action_status = MissionStatus()
                results.append(f"{label}{'✓' if action_status else '✕'}")
                if await expiry.handle(action_status, "app", f"米游币{label}"):
                    login_expired = True
                    break
            if results:
                action_results.append(f"{mission.name} · {' '.join(results)}")
            if login_expired:
                break
            if attempted:
                status, progress = await get_missions_state(account)
                if await expiry.handle(status, "app", "查询米游币任务进度"):
                    login_expired = True
                    break

        if not matcher and (not user.enable_notice or (
            login_expired and not any("✕" not in result for result in board_results)
        )):
            continue
        final = None
        if not login_expired:
            status, final = await get_missions_state(account)
            await expiry.handle(status, "app", "查询米游币任务结果")
        names = {BaseMission.SIGN: "📅签到", BaseMission.VIEW: "📰阅读",
                 BaseMission.LIKE: "❤️点赞", BaseMission.SHARE: "↗️分享"}
        if final is None or final.current_myb is None or not (final.state_dict or final.unconfirmed):
            title = "⚠️今日米游币任务进度暂未查到"
        elif not final.unconfirmed and all(
            count >= mission.threshold for mission, count in final.state_dict.values()
        ) and all("✕" not in result for result in board_results):
            title = "🎉已完成今日米游币任务"
        else:
            title = "⚠️今日米游币任务未全部完成"
        lines = [title, f"🆔账户 {account.display_name}"]
        if final is not None:
            lines.extend(f"{names.get(key, key)}：{'✓' if count >= mission.threshold else '✕'}"
                         for key, (mission, count) in final.state_dict.items())
            lines.extend(f"{names.get(key, key)}：暂未查到" for key in final.unconfirmed)
        lines.extend(board_results)
        if matcher:
            lines.extend(action_results)
        if not board_results and not known_boards:
            lines.append("未设置可执行的米游币分区。")
        final_points = final.current_myb if final is not None else None
        earned = final_points - initial_points if final_points is not None and initial_points is not None else "暂未查到"
        lines.extend((f"🪙获得米游币: {earned}",
                      f"💰当前米游币: {final_points if final_points is not None else '暂未查到'}"))
        if matcher:
            message = "\n".join(lines)
            if isinstance(event, OneBotV11GroupMessageEvent):
                await send_qqGroup(bot, event, [message])
            else:
                await matcher.send(message)
        else:
            lines.append(web_token_expiry_notice(account.web_scanned_at))
            for user_id in user_ids:
                await send_private_msg(user_id=user_id, message="\n".join(lines))


async def _account_notes(account, game, owner_id, matcher, cache):
    operation = f"查询{game.name}实时便笺"
    expiry = CredentialExpiry(owner_id, account, reply=matcher.send if matcher else None)
    if await expiry.skip("web", operation):
        return []
    key = (account.bbs_uid, account.device_id_ios, credential_fingerprint(account, "web"))
    records = cache.get(key)
    if records is None:
        status, records = await get_bound_game_roles(account)
        if not status:
            if not await expiry.handle(status, "web", operation) and matcher:
                await matcher.send(f"⚠️账户 {account.display_name} 暂无法验证{game.name}绑定角色。")
            return []
        cache[key] = records
    candidates = [record for record in records if record.game_id == game.game_id]
    if not candidates:
        if matcher:
            await matcher.send(f"⚠️账户 {account.display_name} 没有查询到{game.name}角色。")
        return []
    targets = candidates if matcher else select_game_roles(account, game, records)
    if not targets:
        return []
    status, notes, family = await get_role_notes(account, game.game_id, candidates, targets)
    expired = await expiry.handle(status, family, operation)
    if matcher:
        if not notes and not expired:
            reason = "需要人机验证" if status.need_verify else "暂无法查询"
            await matcher.send(f"⚠️账户 {account.display_name} 的{game.name}便笺{reason}。")
        confirmed = {role_key(role) for role, _ in notes if role is not None}
        missing = [role_label(role) for role in targets if role_key(role) not in confirmed]
        if notes and missing:
            await matcher.send(f"以下角色的便笺暂未确认：{'、'.join(missing)}")
        return notes
    target_keys = {role_key(role) for role in targets}
    return [(role, note) for role, note in notes if role is not None and role_key(role) in target_keys]


async def _send_note(user_ids, matcher, message):
    if matcher:
        await matcher.send(message)
    else:
        for recipient in user_ids:
            await send_private_msg(user_id=recipient, message=message)


def _note_value(value):
    return value if value is not None else "未知"


async def genshin_note_check(user: UserData, user_ids: Iterable[str], matcher: Matcher = None, *, owner_id: str,
                             bindings_cache=None):
    cache = {} if bindings_cache is None else bindings_cache
    for account in user.accounts.values():
        if not matcher and not (account.enable_resin and "GenshinImpact" in account.game_sign_games):
            continue
        notes = await _account_notes(account, GenshinImpactSign, owner_id, matcher, cache)
        for role, note in notes:
            capacity = note.max_resin or 200
            alerts = []
            if not matcher:
                key = (owner_id, account.bbs_uid, GenshinImpactSign.game_id, *role_key(role))
                notice = note_notice_status.setdefault(key, NoteNoticeStatus()).genshin
                if note.current_resin >= capacity:
                    if not notice.current_resin_full:
                        alerts.append("❕您的树脂已经满啦")
                    notice.current_resin_full = True
                elif note.current_resin >= account.user_resin_threshold:
                    if not notice.current_resin:
                        alerts.append("❕您的树脂已达到提醒阈值")
                    notice.current_resin, notice.current_resin_full = True, False
                else:
                    notice.current_resin = notice.current_resin_full = False
                if note.current_home_coin is not None and note.max_home_coin:
                    is_full = note.current_home_coin >= note.max_home_coin
                    if is_full and not notice.current_home_coin:
                        alerts.append("❕您的洞天财瓮已经满啦")
                    notice.current_home_coin = is_full
                if note.transformer:
                    ready = note.transformer_text == "已准备就绪"
                    if ready and not notice.transformer:
                        alerts.append("❕您的参量质变仪已准备就绪")
                    notice.transformer = ready
                if not alerts:
                    continue
            tasks = 4 - note.finished_task_num if note.finished_task_num is not None else "未知"
            lines = [*alerts, "❖原神·实时便笺❖", f"🆔账户 {account.display_name}",
                     f"角色：{role_label(role) if role else '角色未确认'}",
                     f"⏳树脂数量：{note.current_resin} / {capacity}", f"⏱️树脂{note.resin_recovery_text}",
                     f"🕰️探索派遣：{_note_value(note.current_expedition_num)} / {_note_value(note.max_expedition_num)}",
                     f"📅每日委托：{tasks} 个任务未完成",
                     f"💰洞天财瓮：{_note_value(note.current_home_coin)} / {_note_value(note.max_home_coin)}",
                     f"🎰参量质变仪：{note.transformer_text if note.transformer else '未知'}"]
            await _send_note(user_ids, matcher, "\n".join(lines))


async def starrail_note_check(user: UserData, user_ids: Iterable[str], matcher: Matcher = None, *, owner_id: str,
                              bindings_cache=None):
    cache = {} if bindings_cache is None else bindings_cache
    for account in user.accounts.values():
        if not matcher and not (account.enable_resin and "StarRail" in account.game_sign_games):
            continue
        notes = await _account_notes(account, StarRailSign, owner_id, matcher, cache)
        for role, note in notes:
            alerts = []
            if not matcher:
                key = (owner_id, account.bbs_uid, StarRailSign.game_id, *role_key(role))
                notice = note_notice_status.setdefault(key, NoteNoticeStatus()).starrail
                if note.max_stamina is not None and note.current_stamina >= note.max_stamina:
                    if not notice.current_stamina_full:
                        alerts.append("❕您的开拓力已经溢出")
                    notice.current_stamina_full = True
                elif note.current_stamina >= account.user_stamina_threshold:
                    if not notice.current_stamina:
                        alerts.append("❕您的开拓力已达到提醒阈值")
                    notice.current_stamina, notice.current_stamina_full = True, False
                else:
                    notice.current_stamina = notice.current_stamina_full = False
                if alerts and note.current_train_score is not None and note.max_train_score is not None:
                    if note.current_train_score < note.max_train_score:
                        alerts.append("❕您的每日实训未完成")
                if plugin_config.preference.notice_time and note.current_rogue_score is not None:
                    if note.max_rogue_score is not None and note.current_rogue_score < note.max_rogue_score:
                        alerts.append("❕您的模拟宇宙积分还没打满")
                if not alerts:
                    continue
            lines = [*alerts, "❖星穹铁道·实时便笺❖", f"🆔账户 {account.display_name}",
                     f"角色：{role_label(role) if role else '角色未确认'}",
                     f"⏳开拓力数量：{note.current_stamina} / {_note_value(note.max_stamina)}",
                     f"⏱开拓力{note.stamina_recover_text}",
                     f"📒每日实训：{_note_value(note.current_train_score)} / {_note_value(note.max_train_score)}",
                     f"📅每日委托：{_note_value(note.accepted_expedition_num)} / {_note_value(note.total_expedition_num or 4)}",
                     f"🌌模拟宇宙：{_note_value(note.current_rogue_score)} / {_note_value(note.max_rogue_score)}"]
            await _send_note(user_ids, matcher, "\n".join(lines))


async def send_qqGroup(bot, event, msgs_list):
    def build_forward_msg(msg):
        # 受限于LLOnebot，合并转发消息只能使用bot的身份无法自定义
        return {"type": "node", "data": {"nickname": "流萤", "user_id": "114514", "content": msg}}

    messages = [build_forward_msg(msg) for msg in msgs_list]
    await bot.call_api("send_group_msg", group_id=event.group_id,
                       message={"type": "at", "data": {"qq": str(event.user_id)}})
    await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=messages)


@scheduler.scheduled_job("cron", hour='0', minute='0', id="daily_goodImg_update")
def daily_update():
    """
    每日图片生成函数
    """
    logger.info(f"{plugin_config.preference.log_head}后台开始生成每日商品图片")
    threading.Thread(target=generate_image).start()


@scheduler.scheduled_job("cron",
                         hour=plugin_config.preference.plan_time.split(':')[0],
                         minute=plugin_config.preference.plan_time.split(':')[1],
                         id="daily_schedule")
@data_session
async def daily_schedule():
    """
    自动米游币任务、游戏签到函数
    """
    logger.info(f"{plugin_config.preference.log_head}开始执行每日自动任务")
    for user_id, user in get_unique_users():
        user_ids = [user_id] + list(get_all_bind(user_id))
        for uid, account in user.accounts.items():
            account_user = user.copy(update={"accounts": {uid: account}})
            for task in (perform_game_sign, perform_bbs_sign):
                try:
                    await task(user=account_user, user_ids=user_ids, owner_id=user_id)
                except Exception:
                    logger.exception(f"{task.__name__}执行异常，继续处理其他账号和任务")
    logger.info(f"{plugin_config.preference.log_head}每日自动任务执行完成")


@scheduler.scheduled_job("interval",
                         minutes=plugin_config.preference.resin_interval,
                         id="resin_check")
@data_session
async def auto_note_check():
    """
    自动查看实时便笺
    """
    logger.info(f"{plugin_config.preference.log_head}开始执行自动便笺检查")
    for user_id, user in get_unique_users():
        user_ids = [user_id] + list(get_all_bind(user_id))
        cache = {}
        for uid, account in user.accounts.items():
            account_user = user.copy(update={"accounts": {uid: account}})
            for task in (genshin_note_check, starrail_note_check):
                try:
                    await task(user=account_user, user_ids=user_ids, owner_id=user_id, bindings_cache=cache)
                except Exception:
                    logger.exception(f"{task.__name__}执行异常，继续处理其他账号")
    logger.info(f"{plugin_config.preference.log_head}自动便笺检查执行完成")
