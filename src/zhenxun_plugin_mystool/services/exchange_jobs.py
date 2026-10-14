import asyncio
from datetime import datetime
import time

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from apscheduler.jobstores.base import JobLookupError
from nonebot_plugin_apscheduler import scheduler
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from ..api.common import get_good_detail, good_exchange_sync
from ..model.config import plugin_config
from ..storage import PluginDataManager, data_session
from ..storage.codec import plan_key
from ..utils import get_all_bind, logger, send_private_msg

_jobs = {}
_results = {}
_locks = {}


def schedule_plan(user_id, plan):
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
            run_date=datetime.fromtimestamp(plan.good.time),
            max_instances=plugin_config.preference.exchange_thread_count,
        )


@data_session
async def execute_exchange_job(user_id, identity):
    user = PluginDataManager.plugin_data.users.get(user_id)
    plan = (
        next((item for item in user.exchange_plans if plan_key(item) == identity), None)
        if user
        else None
    )
    if plan is None:
        return False, None
    return await asyncio.to_thread(good_exchange_sync, plan)


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
    status, result = event.retval if not event.exception else (False, None)
    success = bool(status and result and result.result)
    already_succeeded = any(results.values())
    completed = {**results, index: success}
    if len(completed) == plugin_config.preference.exchange_thread_count:
        user.exchange_plans.remove(plan)
        await PluginDataManager.save()
    results[index] = success
    _jobs.pop(event.job_id, None)
    if not already_succeeded:
        text = "兑换成功" if success else "兑换失败" if status else "兑换请求发送失败"
        for recipient in [user_id, *get_all_bind(user_id)]:
            await send_private_msg(
                recipient,
                f"账户 {plan.account.display_name}\n{plan.good.general_name}\n线程 {index + 1}\n{text}",
            )
    if len(completed) == plugin_config.preference.exchange_thread_count:
        _results.pop(key, None)


@PriorityLifecycle.on_startup(priority=3)
@data_session
async def restore_exchange_jobs():
    schedules = []
    for user_id, user in PluginDataManager.plugin_data.users.items():
        if user_id in PluginDataManager.plugin_data.user_bind:
            continue
        for plan in list(user.exchange_plans):
            status, good = await get_good_detail(plan.good.goods_id)
            if not status:
                if getattr(status, "good_not_existed", False):
                    user.exchange_plans.remove(plan)
                else:
                    logger.warning(
                        "商品查询失败，保留数据库兑换计划",
                        session=user_id,
                        target=plan.good.goods_id,
                    )
                    if plan.good.time and plan.good.time >= time.time():
                        schedules.append((user_id, plan))
                continue
            if good is None:
                logger.warning(
                    "商品查询未返回详情，保留兑换计划",
                    session=user_id,
                    target=plan.good.goods_id,
                )
                continue
            if not good.time or good.time < time.time():
                user.exchange_plans.remove(plan)
            else:
                user.exchange_plans.remove(plan)
                plan.good = good
                user.exchange_plans.add(plan)
                schedules.append((user_id, plan))
    await PluginDataManager.save()
    for user_id, plan in schedules:
        schedule_plan(user_id, plan)
    logger.info("mystool 兑换任务已从数据库恢复")


scheduler.add_listener(on_exchange_result, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
