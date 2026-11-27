import asyncio
from datetime import datetime, timezone
import time

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.jobstores.base import JobLookupError
from nonebot_plugin_apscheduler import scheduler
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from ..api.common import get_good_detail, good_exchange_sync
from ..model.config import plugin_config
from ..storage import PluginDataManager, data_session
from ..storage.codec import plan_key
from ..utils import COMMAND_BEGIN, get_all_bind, get_last_command_sep, logger, send_private_msg
from .credential_expiry import CredentialExpiry

_jobs = {}
_results = {}
_locks = {}


def plan_pause_reason(plan):
    if plan.paused_reason:
        return plan.paused_reason
    if plan.scheduled_at is None:
        return "schedule_unconfirmed"
    if plan.scheduled_at <= time.time():
        return "schedule_passed"
    return None


async def hydrate_game_biz(plan):
    if plan.game_record is not None and not plan.good.game_biz:
        status, good = await get_good_detail(plan.good.goods_id)
        if status and good is not None:
            plan.good.game_biz = good.game_biz


def schedule_plan(user_id, plan):
    if reason := plan_pause_reason(plan):
        plan.paused_reason = reason
        return False
    user_id = PluginDataManager.plugin_data.user_bind.get(user_id, user_id)
    key = (user_id, plan_key(plan))
    _results.setdefault(key, {})
    for index in range(plugin_config.preference.exchange_thread_count):
        job_id = f"exchange-plan-{key[1]}-{user_id}-{index}"
        _jobs[job_id] = (key, index)
        scheduler.add_job(
            execute_exchange_job,
            "date",
            id=job_id,
            args=key,
            replace_existing=True,
            run_date=datetime.fromtimestamp(plan.scheduled_at, timezone.utc),
            max_instances=plugin_config.preference.exchange_thread_count,
        )
    return True


@data_session
async def execute_exchange_job(user_id, identity):
    user = PluginDataManager.plugin_data.users.get(user_id)
    plan = (
        next((item for item in user.exchange_plans if plan_key(item) == identity), None)
        if user
        else None
    )
    if plan is None or plan.paused_reason or plan.scheduled_at is None:
        return False, None
    status, result = await asyncio.to_thread(good_exchange_sync, plan)
    if status.login_expired:
        await CredentialExpiry(user_id, plan.account).handle(status, "combined", "商品兑换")
    return status, result


def cancel_plan(user_id, plan):
    user_id = PluginDataManager.plugin_data.user_bind.get(user_id, user_id)
    key = (user_id, plan_key(plan))
    for job_id, (registered, _) in list(_jobs.items()):
        if registered == key:
            try:
                scheduler.remove_job(job_id)
            except JobLookupError:
                pass
            _jobs.pop(job_id, None)
    _results.pop(key, None)


def on_exchange_result(event):
    if event.job_id in _jobs:
        scheduler.add_job(
            process_exchange_result,
            "date",
            args=(event,),
            id="mystool-result-" + event.job_id,
            replace_existing=True,
            misfire_grace_time=None,
        )


async def process_exchange_result(event):
    entry = _jobs.get(event.job_id)
    if entry is None:
        return
    key = (asyncio.get_running_loop(), entry[0])
    async with _locks.setdefault(key, asyncio.Lock()):
        await _process_exchange_result(event)


@data_session
async def _process_exchange_result(event):
    entry = _jobs.get(event.job_id)
    if entry is None:
        return
    key, index = entry
    user_id, identity = key
    user = PluginDataManager.plugin_data.users.get(user_id)
    plan = (
        next((item for item in user.exchange_plans if plan_key(item) == identity), None)
        if user
        else None
    )
    if plan is None:
        _jobs.pop(event.job_id, None)
        return
    results = _results.setdefault(key, {})
    if index in results:
        return
    missed = event.code == EVENT_JOB_MISSED
    status, result = (False, None) if missed or event.exception else event.retval
    success = bool(status and result and result.result)
    already_succeeded = any(results.values())
    outcome = None if missed else success
    completed = {**results, index: outcome}
    finished = len(completed) == plugin_config.preference.exchange_thread_count
    all_missed = finished and all(value is None for value in completed.values())
    if finished:
        if all_missed:
            plan.paused_reason = "schedule_passed"
        else:
            user.exchange_plans.remove(plan)
        await PluginDataManager.save()
        _results.pop(key, None)
    else:
        results[index] = outcome
    _jobs.pop(event.job_id, None)
    if all_missed:
        hint = f"{COMMAND_BEGIN}兑换{get_last_command_sep()}+ {plan.good.goods_id}"
        text = f"兑换场次已错过。请使用『{hint}』确认新场次。"
    elif missed or already_succeeded or getattr(status, "login_expired", False):
        return
    else:
        result_text = "兑换成功" if success else "兑换失败" if status else "兑换请求发送失败"
        text = f"线程 {index + 1}\n{result_text}"
    for recipient in [user_id, *get_all_bind(user_id)]:
        await send_private_msg(recipient, f"账户 {plan.account.display_name}\n{plan.good.general_name}\n{text}")


@PriorityLifecycle.on_startup(priority=3)
@data_session
async def restore_exchange_jobs():
    schedules = []
    for user_id, user in PluginDataManager.plugin_data.users.items():
        if user_id in PluginDataManager.plugin_data.user_bind:
            continue
        for plan in user.exchange_plans:
            if reason := plan_pause_reason(plan):
                plan.paused_reason = reason
                continue
            await hydrate_game_biz(plan)
            schedules.append((user_id, plan))
    await PluginDataManager.save()
    for user_id, plan in schedules:
        schedule_plan(user_id, plan)
    logger.info("mystool 兑换任务已从数据库恢复")


scheduler.add_listener(on_exchange_result, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
