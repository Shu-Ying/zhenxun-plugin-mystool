import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.executors.base import run_coroutine_job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot.exception import FinishedException
import pytest
from tortoise import Tortoise


NOW = 1_900_000_000
PAST = NOW - 1_000
FUTURE = NOW + 1_000
LATER = NOW + 2_000
OWNER = "owner"
UID = "42"
GOODS_ID = "123"


@pytest.fixture
def exchange(mystool, monkeypatch):
    package = mystool.__name__
    model = import_module(f"{package}.model.data")
    codec = import_module(f"{package}.storage.codec")
    storage = import_module(f"{package}.storage.session")
    jobs = import_module(f"{package}.services.exchange_jobs")
    registered_scheduler = jobs.scheduler
    monkeypatch.setattr(jobs.time, "time", lambda: NOW)
    monkeypatch.setattr(jobs, "scheduler", AsyncIOScheduler())
    monkeypatch.setattr(jobs, "_jobs", {})
    monkeypatch.setattr(jobs, "_results", {})
    monkeypatch.setattr(jobs, "_locks", {})
    monkeypatch.setattr(jobs, "send_private_msg", AsyncMock())
    monkeypatch.setattr(jobs, "good_exchange_sync", _reject_redemption)
    monkeypatch.setattr(jobs, "get_good_detail", AsyncMock(side_effect=AssertionError("Unexpected goods query")))
    return SimpleNamespace(
        model=model, codec=codec, storage=storage, jobs=jobs, package=package,
        registered_scheduler=registered_scheduler,
    )


def _reject_redemption(*args):
    pytest.fail("A scheduling test attempted to redeem goods")


async def _dispatch_result(exchange, job, code, retval=None):
    event = JobExecutionEvent(
        code, job.id, "default", datetime.fromtimestamp(FUTURE, timezone.utc), retval=retval,
    )
    exchange.registered_scheduler._dispatch_event(event)
    handler = exchange.jobs.scheduler.get_job(f"mystool-result-{job.id}")
    assert handler is not None
    await handler.func(*handler.args)


def _plan(exchange, sale_start, *, virtual=False):
    return exchange.model.ExchangePlan(
        account=exchange.model.UserAccount(
            cookies=exchange.model.BBSCookies(stuid=UID),
            device_id_ios="device-ios", device_id_android="device-android", device_fp="fixture-fp",
        ),
        good={
            "goods_id": GOODS_ID, "name": "测试商品", "type": 2 if virtual else 1, "price": 100, "icon": "",
            "sale_start_time": sale_start, "next_time": LATER,
            "account_exchange_num": 0, "account_cycle_limit": 1, "account_cycle_type": "forever",
        },
        game_record={
            "region_name": "测试区", "game_id": 2, "level": 10, "region": "cn_gf01",
            "game_role_id": "1001", "nickname": "测试玩家",
        } if virtual else None,
    )


def _old_id(slot, role=None):
    identity = [GOODS_ID, slot, None, UID, role]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _records(exchange, plan):
    user = exchange.model.UserData(accounts={UID: plan.account}, exchange_plans=[plan], enable_notice=False)
    return exchange.codec.encode(exchange.model.PluginData(users={OWNER: user}))


def _legacy_records(exchange, sale_start, hashed_slot, *, virtual=False):
    row_id = _old_id(hashed_slot, role="1001" if virtual else None)
    records = _records(exchange, _plan(exchange, sale_start, virtual=virtual))
    plan_payload = next(iter(records["plans"].values()))
    records["plans"] = {
        (OWNER, row_id): {
            name: plan_payload[name] for name in ("good", "address", "game_record", "bbs_uid")
        }
    }
    return row_id, records


async def _with_database(exchange, records, operation):
    await Tortoise.init(
        db_url="sqlite://:memory:", modules={"models": [f"{exchange.package}.storage.models"]}
    )
    try:
        await Tortoise.generate_schemas()
        await exchange.storage.initialize_storage()
        repository = exchange.storage.Repository
        await repository.commit(await repository.load(), records)
        return await operation()
    finally:
        await Tortoise.close_connections()


@pytest.mark.parametrize("async_call", [True, False])
def test_exchange_request_does_not_modify_shared_device_headers(exchange, monkeypatch, async_call):
    import httpx

    api = import_module(f"{exchange.package}.api.common")
    plan = _plan(exchange, FUTURE)
    plan.account.device_fp = "test-fingerprint"
    before = api.HEADERS_EXCHANGE.copy()
    seen = []

    def receive(request):
        seen.append(request.headers)
        return httpx.Response(200, json={"retcode": -100, "message": "登录失效，请重新登录"})

    client_type = httpx.AsyncClient if async_call else httpx.Client
    name = "AsyncClient" if async_call else "Client"
    monkeypatch.setattr(httpx, name, lambda: client_type(transport=httpx.MockTransport(receive)))
    if async_call:
        status, _ = asyncio.run(api.good_exchange(plan))
    else:
        status, _ = api.good_exchange_sync(plan)

    assert status.login_expired is True
    assert len(seen) == 1
    assert seen[0]["x-rpc-device_id"] == "device-ios"
    assert seen[0]["x-rpc-device_fp"] == "test-fingerprint"
    assert api.HEADERS_EXCHANGE == before


def test_confirmed_plan_keeps_identity_when_goods_time_moves_to_next_round(exchange):
    plan = _plan(exchange, FUTURE)
    plans = {plan}
    assert exchange.codec.plan_key(plan) == _old_id(FUTURE)

    plan.good.sale_start_time = PAST
    assert plan.good.time == LATER

    assert plan in plans
    assert exchange.codec.plan_key(plan) == _old_id(FUTURE)
    saved = exchange.codec.decode(_records(exchange, plan))
    restored = next(iter(saved.users[OWNER].exchange_plans))
    assert restored.scheduled_at == FUTURE


@pytest.mark.parametrize(
    ("saved_sale_start", "hashed_slot", "expected_slot", "expected_reason"),
    [
        (PAST, PAST, PAST, "schedule_passed"),
        (FUTURE, FUTURE, FUTURE, None),
        (PAST, LATER, None, "schedule_unconfirmed"),
        (None, LATER, LATER, None),
        (FUTURE, FUTURE + 1, None, "schedule_unconfirmed"),
    ],
)
def test_startup_preserves_legacy_identity_and_only_schedules_original_future_slot(
    exchange, monkeypatch, saved_sale_start, hashed_slot, expected_slot, expected_reason
):
    row_id, records = _legacy_records(exchange, saved_sale_start, hashed_slot, virtual=True)
    latest = _plan(exchange, LATER, virtual=True).good
    latest.game_biz = "hk4e_cn"
    common = import_module(f"{exchange.package}.model.common")
    monkeypatch.setattr(
        exchange.jobs, "get_good_detail",
        AsyncMock(return_value=(common.GetGoodDetailStatus(success=True), latest)),
    )

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))
    plans = stored["plans"]

    assert list(plans) == [(OWNER, row_id)]
    assert stored["users"][OWNER]["enable_notice"] is False
    assert stored["accounts"] == records["accounts"]
    assert plans[(OWNER, row_id)]["scheduled_at"] == expected_slot
    assert plans[(OWNER, row_id)]["paused_reason"] == expected_reason
    assert plans[(OWNER, row_id)]["good"]["sale_start_time"] == saved_sale_start
    assert plans[(OWNER, row_id)]["good"]["game_biz"] == ("hk4e_cn" if expected_reason is None else None)
    assert exchange.codec.encode(exchange.codec.decode(stored)) == stored
    scheduled = [job.trigger.run_date.timestamp() for job in exchange.jobs.scheduler.get_jobs()]
    assert scheduled == ([expected_slot] * exchange.jobs.plugin_config.preference.exchange_thread_count
                         if expected_reason is None else [])
    if expected_reason is not None:
        exchange.jobs.get_good_detail.assert_not_awaited()


@pytest.mark.parametrize("notice_enabled", [True, False])
def test_scheduled_exchange_login_expiry_honors_notice_setting(exchange, monkeypatch, notice_enabled):
    plan = _plan(exchange, FUTURE)
    records = _records(exchange, plan)
    records["users"][OWNER]["enable_notice"] = notice_enabled
    common = import_module(f"{exchange.package}.model.common")
    expiry = import_module(f"{exchange.package}.services.credential_expiry")
    expiry_notice = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(expiry, "send_private_msg", expiry_notice)
    monkeypatch.setattr(exchange.jobs.plugin_config.preference, "exchange_thread_count", 2)

    def expired_exchange(scheduled_plan):
        assert scheduled_plan.scheduled_at == FUTURE
        assert scheduled_plan.account.bbs_uid == UID
        return common.ExchangeStatus(login_expired=True), None

    monkeypatch.setattr(exchange.jobs, "good_exchange_sync", expired_exchange)

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        for job in exchange.jobs.scheduler.get_jobs():
            await _dispatch_result(exchange, job, EVENT_JOB_EXECUTED, await job.func(*job.args))

    asyncio.run(_with_database(exchange, records, run))

    exchange.jobs.send_private_msg.assert_not_awaited()
    if notice_enabled:
        expiry_notice.assert_awaited_once()
        assert expiry_notice.await_args.kwargs["user_id"] == OWNER
        message = expiry_notice.await_args.kwargs["message"]
        assert "登录失效" in message
        assert f"登录 {UID}" in message
    else:
        expiry_notice.assert_not_awaited()


@pytest.mark.parametrize("completed", [None, True, False])
@pytest.mark.parametrize("missed_first", [True, False])
def test_missed_exchange_workers_only_pause_the_plan_if_no_worker_ran(
    exchange, monkeypatch, completed, missed_first
):
    plan = _plan(exchange, FUTURE)
    records = _records(exchange, plan)
    monkeypatch.setattr(exchange.jobs.plugin_config.preference, "exchange_thread_count", 2)
    common = import_module(f"{exchange.package}.model.common")

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        first, second = exchange.jobs.scheduler.get_jobs()
        missed, other = (first, second) if missed_first else (second, first)
        events = [(missed, EVENT_JOB_MISSED, None)]
        if completed is None:
            events.append((other, EVENT_JOB_MISSED, None))
        else:
            result = exchange.model.ExchangeResult(result=completed, return_data={}, plan=plan)
            events.append((other, EVENT_JOB_EXECUTED, (common.ExchangeStatus(success=True), result)))
        for job, code, result in events if missed_first else reversed(events):
            await _dispatch_result(exchange, job, code, result)
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    exchange.jobs.send_private_msg.assert_awaited_once()
    if completed is None:
        row = stored["plans"][(OWNER, _old_id(FUTURE))]
        assert row["scheduled_at"] == FUTURE
        assert row["paused_reason"] == "schedule_passed"
        assert "错过" in exchange.jobs.send_private_msg.await_args.args[1]
    else:
        assert not stored["plans"]
        message = "兑换成功" if completed else "兑换失败"
        assert message in exchange.jobs.send_private_msg.await_args.args[1]
    assert not exchange.jobs._jobs
    assert not exchange.jobs._results


def test_delayed_exchange_result_callback_still_records_the_captured_outcome(exchange, monkeypatch):
    records = _records(exchange, _plan(exchange, FUTURE))
    monkeypatch.setattr(exchange.jobs.plugin_config.preference, "exchange_thread_count", 1)

    async def run():
        scheduler = exchange.jobs.scheduler
        scheduler.start(paused=True)
        try:
            await exchange.jobs.restore_exchange_jobs()
            worker, = scheduler.get_jobs()
            event = JobExecutionEvent(EVENT_JOB_MISSED, worker.id, "default", worker.next_run_time)
            exchange.registered_scheduler._dispatch_event(event)
            handler = scheduler.get_job(f"mystool-result-{worker.id}")
            late = handler.next_run_time - timedelta(minutes=1)
            result, = await run_coroutine_job(handler, "default", [late], __name__)
            assert result.code == EVENT_JOB_EXECUTED
            return await exchange.storage.Repository.load()
        finally:
            scheduler.shutdown()
            await asyncio.sleep(0)

    stored = asyncio.run(_with_database(exchange, records, run))

    assert stored["plans"][(OWNER, _old_id(FUTURE))]["paused_reason"] == "schedule_passed"


def test_exchange_command_tells_user_how_to_reconfirm_an_unknown_slot(exchange):
    row_id, records = _legacy_records(exchange, FUTURE, FUTURE + 1)
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(finish=AsyncMock())
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        await command.myb_exchange_plan.handlers[0].call(
            event=event, matcher=matcher, state={}, command=("兑换",), command_arg=None,
        )
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    message, = matcher.finish.await_args.args
    assert "原兑换时间无法确认" in message
    assert f"兑换{command.get_last_command_sep()}+ {GOODS_ID}" in message
    assert list(stored["plans"]) == [(OWNER, row_id)]


def test_exchange_confirmation_rejects_a_slot_that_passed_during_selection(exchange):
    plan = _plan(exchange, PAST)
    records = _records(exchange, plan)
    records["plans"].clear()
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-1].call(
                event=event, matcher=matcher, state={
                    "good": plan.good, "scheduled_at": PAST, "bbs_uid": UID,
                }, game_uid="",
            )
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    message, = matcher.finish.await_args.args
    assert "场次已过" in message
    assert not stored["plans"]
    assert not exchange.jobs.scheduler.get_jobs()


@pytest.mark.parametrize("old_slot", [FUTURE + 1, FUTURE + 2])
def test_exchange_confirmation_replaces_only_paused_plans_for_the_same_destination(exchange, old_slot):
    original_slot = FUTURE + 1
    _, records = _legacy_records(exchange, FUTURE, old_slot)
    unrelated_id, unrelated = _legacy_records(exchange, FUTURE, old_slot, virtual=True)
    records["plans"].update(unrelated["plans"])
    plan = _plan(exchange, original_slot)
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        async with exchange.storage.PluginDataManager.session() as session:
            with pytest.raises(FinishedException):
                await command.myb_exchange_plan.handlers[-1].call(
                    event=event, matcher=matcher, state={
                        "good": plan.good, "scheduled_at": original_slot, "bbs_uid": UID,
                    }, game_uid="",
                )
            assert len(session.data.users[OWNER].exchange_plans) == 2
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    row_id = _old_id(original_slot)
    assert set(stored["plans"]) == {(OWNER, row_id), (OWNER, unrelated_id)}
    assert stored["plans"][(OWNER, row_id)]["scheduled_at"] == original_slot
    assert stored["plans"][(OWNER, row_id)]["paused_reason"] is None
    assert len(exchange.jobs.scheduler.get_jobs()) == exchange.jobs.plugin_config.preference.exchange_thread_count


def test_exchange_deletion_requests_selection_when_goods_id_matches_multiple_plans(exchange):
    active = _plan(exchange, FUTURE)
    row_id, records = _legacy_records(exchange, FUTURE, FUTURE + 1)
    records["plans"].update(_records(exchange, active)["plans"])
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)
    state = {"command_2": "-", "bbs_uid": UID}

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        await command.myb_exchange_plan.handlers[-2].call(
            event=event, matcher=matcher, state=state, good_id=GOODS_ID,
        )
        before = await exchange.storage.Repository.load()
        assert len(before["plans"]) == 2
        message, = matcher.send.await_args.args
        paused_line = next(line for line in message.splitlines() if "待确认" in line)
        choice = paused_line.split(".", 1)[0]
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-1].call(
                event=event, matcher=matcher, state=state, game_uid=choice,
            )
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    assert (OWNER, row_id) not in stored["plans"]
    assert list(stored["plans"]) == [(OWNER, _old_id(FUTURE))]
    assert len(exchange.jobs.scheduler.get_jobs()) == exchange.jobs.plugin_config.preference.exchange_thread_count


def test_exchange_deletion_rejects_a_choice_after_the_user_is_unbound(exchange):
    active = _plan(exchange, FUTURE)
    _, records = _legacy_records(exchange, FUTURE, FUTURE + 1)
    records["plans"].update(_records(exchange, active)["plans"])
    records["bindings"]["alias"] = OWNER
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: "alias")
    state = {"command_2": "-", "bbs_uid": UID}

    async def run():
        await command.myb_exchange_plan.handlers[-2].call(
            event=event, matcher=matcher, state=state, good_id=GOODS_ID,
        )
        await exchange.storage.Repository.update(lambda rows: rows["bindings"].pop("alias"))
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-1].call(
                event=event, matcher=matcher, state=state, game_uid="1",
            )
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    assert "绑定已变化" in matcher.finish.await_args.args[0]
    assert len(stored["plans"]) == 2


def test_virtual_exchange_without_game_records_prompts_player_to_bind_game(exchange, monkeypatch):
    plan = _plan(exchange, FUTURE, virtual=True)
    records = _records(exchange, plan)
    command = import_module(f"{exchange.package}.command.exchange")
    monkeypatch.setattr(command, "get_good_list", AsyncMock(return_value=(True, [plan.good])))
    monkeypatch.setattr(command, "get_game_record", AsyncMock(return_value=(True, [])))
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-2].call(
                event=event, matcher=matcher, state={"command_2": "+", "bbs_uid": UID}, good_id=GOODS_ID,
            )

    asyncio.run(_with_database(exchange, records, run))

    assert "还没有绑定游戏账号" in matcher.finish.await_args.args[0]
