import asyncio
from datetime import datetime, timezone
import hashlib
import json
import time

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED
from apscheduler.jobstores.base import JobLookupError
from nonebot_plugin_apscheduler import scheduler
from tortoise.transactions import in_transaction
from zhenxun.utils.manager.priority_manager import PriorityLifecycle

from ..api.common import get_good_detail, good_exchange_sync, role_game_id
from ..model.data import ExchangePlan, UserAccount
from ..storage import PluginDataManager, data_session
from ..storage.codec import plan_key
from ..storage.models import MystoolAccount, MystoolExchangeExecution, MystoolPlan, MystoolRevision
from ..storage.repository import Repository
from ..utils import COMMAND_BEGIN, get_last_command_sep, logger, send_private_msg
from .credential_expiry import CredentialExpiry


_jobs = {}
SLOT_GRACE_SECONDS = 1
OUTCOME_TEXT = {"unknown": "兑换结果待核实，请在米游社核对。",
                "success": "兑换成功", "rejected": "米游社已明确拒绝本次兑换",
                "missed": "兑换场次已错过"}


def plan_pause_reason(plan):
    if plan.paused_reason:
        return plan.paused_reason
    if plan.scheduled_at is None:
        return "schedule_unconfirmed"
    if plan.scheduled_at <= time.time():
        return "schedule_passed"
    return None


def redemption_destination(plan):
    role = plan.game_record
    target = (role.game_id, role.region, role.game_role_id) if plan.good.is_virtual and role else None
    return plan.account.bbs_uid, plan.good.goods_id, target


def redemption_key(plan):
    return hashlib.sha256(json.dumps((redemption_destination(plan), plan.scheduled_at)).encode()).hexdigest()


def _valid_exchange_target(plan):
    return (not plan.good.is_virtual or (
        plan.game_record is not None and role_game_id(plan.good.game_biz) == plan.game_record.game_id
    ))


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
    args = (user_id, plan_key(plan), plan.generation)
    job_id = f"exchange-plan-{args[1]}-{user_id}-{plan.generation or 'legacy'}"
    _jobs[job_id] = args
    scheduler.add_job(
        execute_exchange_job, "date", id=job_id, args=args, replace_existing=True,
        run_date=datetime.fromtimestamp(plan.scheduled_at, timezone.utc),
        misfire_grace_time=SLOT_GRACE_SECONDS,
    )
    return True


def _get_plan(records, owner, identity, generation):
    payload = records["plans"].get((owner, identity))
    if payload is None or payload.get("generation") != generation:
        return None
    data = records["accounts"].get((owner, payload["bbs_uid"]))
    if data is not None:
        return ExchangePlan.parse_obj({**payload, "account": UserAccount.parse_obj(data)})
    return None


async def _claim(owner, identity, generation):
    # Use the same locks as plan edits and cancellations. A returned claim
    # has committed, and the key survives plan, account and binding deletion.
    async with Repository.lock(), in_transaction() as connection:
        await (MystoolRevision.filter(key="state").using_db(connection)
               .select_for_update().get())
        row = await MystoolPlan.filter(user_id=owner, plan_id=identity).using_db(connection).get_or_none()
        if row is None or row.data.get("generation") != generation:
            return None, None
        account_row = await MystoolAccount.filter(user_id=owner, bbs_uid=row.data["bbs_uid"]).using_db(connection).get_or_none()
        if account_row is None:
            return None, None
        plan = ExchangePlan.parse_obj({**row.data, "account": UserAccount.parse_obj(account_row.data)})
        if plan.paused_reason or plan.scheduled_at is None or not _valid_exchange_target(plan):
            return None, None
        now = time.time()
        if now < plan.scheduled_at:
            return None, None
        if now > plan.scheduled_at + SLOT_GRACE_SECONDS:
            return plan, "missed"
        key = redemption_key(plan)
        execution, created = await MystoolExchangeExecution.get_or_create(
            key=key, using_db=connection, defaults={
                "owner_id": owner, "plan_id": identity, "generation": generation,
                "scheduled_at": plan.scheduled_at,
                "account_label": plan.account.display_name, "goods_name": plan.good.general_name,
            },
        )
    if created:
        return plan, "claimed"
    if (execution.owner_id, execution.plan_id, execution.generation) == (owner, identity, generation):
        return None, None
    return plan, "duplicate"


async def _finish_plan(owner, identity, generation, *, missed=False):
    def finish(records):
        plan = _get_plan(records, owner, identity, generation)
        if plan is None or plan.paused_reason:
            return None
        if missed:
            records["plans"][(owner, identity)]["paused_reason"] = "schedule_passed"
        else:
            del records["plans"][(owner, identity)]
        return plan

    return await Repository.update(finish)


async def _notify_result(owner, plan, text):
    records = await Repository.load()
    for recipient in [owner, *(source for source, target in records["bindings"].items() if target == owner)]:
        await send_private_msg(recipient, f"账户 {plan.account.display_name}\n{plan.good.general_name}\n{text}")


async def record_missed_slot(owner, identity, generation):
    plan = await _finish_plan(owner, identity, generation, missed=True)
    if plan is not None:
        hint = f"{COMMAND_BEGIN}兑换{get_last_command_sep()}+ {plan.good.goods_id}"
        await _notify_result(owner, plan, f"兑换场次已错过。请使用『{hint}』确认新场次。")


def _send_in_slot(plan):
    if time.time() > plan.scheduled_at + SLOT_GRACE_SECONDS:
        return None
    return good_exchange_sync(plan)


async def execute_exchange_job(owner, identity, generation=None):
    plan, claim = await _claim(owner, identity, generation)
    if plan is None:
        return
    if claim == "missed":
        await record_missed_slot(owner, identity, generation)
        return
    if claim == "duplicate":
        await _finish_plan(owner, identity, generation)
        await _notify_result(owner, plan, "该兑换意图已有执行记录，本次未再次提交。")
        return

    try:
        receipt = await asyncio.to_thread(_send_in_slot, plan)
    except Exception:
        logger.exception("兑换请求结果无法确认")
        receipt = False, None
    key = redemption_key(plan)
    if receipt is None:
        await MystoolExchangeExecution.filter(key=key).update(outcome="missed")
        await record_missed_slot(owner, identity, generation)
        return
    status, result = receipt
    if status and result is not None:
        outcome = "success" if result.result else "rejected"
    elif getattr(status, "login_expired", False):
        outcome = "rejected"
    else:
        outcome = "unknown"
    await MystoolExchangeExecution.filter(key=key).update(outcome=outcome)
    await _finish_plan(owner, identity, generation)
    if getattr(status, "login_expired", False):
        await CredentialExpiry(owner, plan.account).handle(status, "combined", "商品兑换")
    else:
        await _notify_result(owner, plan, OUTCOME_TEXT[outcome])


def cancel_plan(user_id, plan):
    user_id = PluginDataManager.plugin_data.user_bind.get(user_id, user_id)
    args = (user_id, plan_key(plan), plan.generation)
    for job_id, registered in list(_jobs.items()):
        if registered == args:
            try:
                scheduler.remove_job(job_id)
            except JobLookupError:
                pass
            _jobs.pop(job_id, None)


def on_exchange_result(event):
    args = _jobs.pop(event.job_id, None)
    if args is not None and event.code == EVENT_JOB_MISSED:
        scheduler.add_job(
            record_missed_slot, "date", args=args, id="mystool-result-" + event.job_id,
            replace_existing=True, misfire_grace_time=None,
        )


@PriorityLifecycle.on_startup(priority=3)
@data_session
async def restore_exchange_jobs():
    schedules = []
    for user_id, user in PluginDataManager.plugin_data.users.items():
        if user_id in PluginDataManager.plugin_data.user_bind:
            continue
        for plan in list(user.exchange_plans):
            if plan.scheduled_at is not None and await MystoolExchangeExecution.filter(
                key=redemption_key(plan),
            ).exclude(outcome="missed").exists():
                user.exchange_plans.remove(plan)
                continue
            if reason := plan_pause_reason(plan):
                plan.paused_reason = reason
                continue
            await hydrate_game_biz(plan)
            if not _valid_exchange_target(plan):
                plan.paused_reason = "target_unconfirmed"
                continue
            schedules.append((user_id, plan))
    await PluginDataManager.save()
    for user_id, plan in schedules:
        schedule_plan(user_id, plan)
    logger.info("mystool 兑换任务已从数据库恢复")


scheduler.add_listener(on_exchange_result, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED)
