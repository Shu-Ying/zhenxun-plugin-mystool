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
    monkeypatch.setattr(jobs, "send_private_msg", AsyncMock())
    monkeypatch.setattr(jobs, "good_exchange_sync", _reject_redemption)
    monkeypatch.setattr(jobs, "get_good_detail", AsyncMock(side_effect=AssertionError("Unexpected goods query")))
    return SimpleNamespace(
        model=model, codec=codec, storage=storage, jobs=jobs, package=package,
        registered_scheduler=registered_scheduler,
    )


def _reject_redemption(*args):
    pytest.fail("A scheduling test attempted to redeem goods")


async def _dispatch_result(exchange, job, code):
    event = JobExecutionEvent(
        code, job.id, "default", datetime.fromtimestamp(FUTURE, timezone.utc),
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


async def _selected_account_state(exchange, actor=OWNER, **state):
    menus = import_module(f"{exchange.package}.command.account_selection")
    records = await exchange.storage.Repository.load()
    return {**state, "account_menu": menus.AccountMenu.from_records(records, actor), "bbs_uid": UID}


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

    client_type = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda: client_type(transport=httpx.MockTransport(receive)))
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
    assert scheduled == ([expected_slot]
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

    def expired_exchange(scheduled_plan):
        assert scheduled_plan.scheduled_at == FUTURE
        assert scheduled_plan.account.bbs_uid == UID
        return common.ExchangeStatus(login_expired=True), None

    monkeypatch.setattr(exchange.jobs, "good_exchange_sync", expired_exchange)

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        job, = exchange.jobs.scheduler.get_jobs()
        monkeypatch.setattr(exchange.jobs.time, "time", lambda: FUTURE)
        await job.func(*job.args)

    asyncio.run(_with_database(exchange, records, run))

    exchange.jobs.send_private_msg.assert_not_awaited()
    if notice_enabled:
        expiry_notice.assert_awaited_once()
        assert expiry_notice.await_args.kwargs["user_id"] == OWNER
        message = expiry_notice.await_args.kwargs["message"]
        assert "登录失效" in message
        assert "登录』后按序号" in message
    else:
        expiry_notice.assert_not_awaited()


@pytest.mark.parametrize("completed", [None, True, False])
def test_single_exchange_records_completed_or_missed_original_slot(exchange, monkeypatch, completed):
    plan = _plan(exchange, FUTURE)
    common = import_module(f"{exchange.package}.model.common")
    models = import_module(f"{exchange.package}.storage.models")

    async def run():
        await exchange.jobs.restore_exchange_jobs()
        job, = exchange.jobs.scheduler.get_jobs()
        monkeypatch.setattr(exchange.jobs.time, "time", lambda: FUTURE)
        if completed is None:
            await _dispatch_result(exchange, job, EVENT_JOB_MISSED)
            assert not await models.MystoolExchangeExecution.exists()
        else:
            result = exchange.model.ExchangeResult(result=completed, return_data={}, plan=plan)
            monkeypatch.setattr(exchange.jobs, "good_exchange_sync", lambda _: (common.ExchangeStatus(success=True), result))
            await job.func(*job.args)
            row = await models.MystoolExchangeExecution.get()
            assert row.outcome == ("success" if completed else "rejected")
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, _records(exchange, plan), run))
    exchange.jobs.send_private_msg.assert_awaited_once()
    if completed is None:
        assert stored["plans"][(OWNER, _old_id(FUTURE))]["paused_reason"] == "schedule_passed"
        assert "错过" in exchange.jobs.send_private_msg.await_args.args[1]
    else:
        assert not stored["plans"]
        assert ("兑换成功" if completed else "已明确拒绝") in exchange.jobs.send_private_msg.await_args.args[1]


def test_exchange_cannot_resubmit_after_claim_crash_and_recreated_plan(exchange, monkeypatch):
    plan = _plan(exchange, FUTURE)
    models = import_module(f"{exchange.package}.storage.models")
    sends = []

    async def run():
        repository = exchange.storage.Repository
        await exchange.jobs.restore_exchange_jobs()
        job, = exchange.jobs.scheduler.get_jobs()
        monkeypatch.setattr(exchange.jobs.time, "time", lambda: FUTURE)
        # Simulate the process exiting after the durable claim and before a response.
        claims = await asyncio.gather(exchange.jobs._claim(*job.args), exchange.jobs._claim(*job.args))
        assert [state for _, state in claims].count("claimed") == 1
        await repository.update(lambda records: records["plans"].clear())
        replacement = _plan(exchange, FUTURE)
        replacement.scheduled_at = FUTURE
        replacement.generation = "replacement"
        payload = next(iter(_records(exchange, replacement)["plans"].values()))
        await repository.update(lambda records: records["plans"].update({(OWNER, _old_id(FUTURE)): payload}))
        monkeypatch.setattr(exchange.jobs, "good_exchange_sync", lambda value: sends.append(value))
        await job.func(*job.args)
        await exchange.jobs.execute_exchange_job(OWNER, _old_id(FUTURE), replacement.generation)
        await exchange.jobs.restore_exchange_jobs()
        row = await models.MystoolExchangeExecution.get()
        assert row.outcome == "unknown"
        assert not (await repository.load())["plans"]

    asyncio.run(_with_database(exchange, _records(exchange, plan), run))
    assert not sends


def test_redemption_keys_capture_virtual_region_but_ignore_physical_address(exchange):
    physical = _plan(exchange, FUTURE)
    other = physical.copy(update={"address": SimpleNamespace(id=12)})
    assert exchange.jobs.redemption_key(physical) == exchange.jobs.redemption_key(other)
    virtual = _plan(exchange, FUTURE, virtual=True)
    other = virtual.copy(update={"game_record": virtual.game_record.copy(update={"region": "cn_qd01"})})
    assert exchange.jobs.redemption_key(virtual) != exchange.jobs.redemption_key(other)


def test_delayed_exchange_result_callback_still_records_the_captured_outcome(exchange, monkeypatch):
    records = _records(exchange, _plan(exchange, FUTURE))

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
        state = await _selected_account_state(exchange, good=plan.good, scheduled_at=PAST)
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-1].call(
                event=event, matcher=matcher, state=state, game_uid="",
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
        state = await _selected_account_state(exchange, good=plan.good, scheduled_at=original_slot)
        async with exchange.storage.PluginDataManager.session() as session:
            with pytest.raises(FinishedException):
                await command.myb_exchange_plan.handlers[-1].call(
                    event=event, matcher=matcher, state=state, game_uid="",
                )
            assert len(session.data.users[OWNER].exchange_plans) == 2
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    row_id = _old_id(original_slot)
    assert set(stored["plans"]) == {(OWNER, row_id), (OWNER, unrelated_id)}
    assert stored["plans"][(OWNER, row_id)]["scheduled_at"] == original_slot
    assert stored["plans"][(OWNER, row_id)]["paused_reason"] is None
    assert len(exchange.jobs.scheduler.get_jobs()) == 1


def test_exchange_deletion_requests_selection_when_goods_id_matches_multiple_plans(exchange):
    active = _plan(exchange, FUTURE)
    row_id, records = _legacy_records(exchange, FUTURE, FUTURE + 1)
    records["plans"].update(_records(exchange, active)["plans"])
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        state = await _selected_account_state(exchange, command_2="-")
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
    assert len(exchange.jobs.scheduler.get_jobs()) == 1


def test_exchange_deletion_rejects_a_choice_after_the_user_is_unbound(exchange):
    active = _plan(exchange, FUTURE)
    _, records = _legacy_records(exchange, FUTURE, FUTURE + 1)
    records["plans"].update(_records(exchange, active)["plans"])
    records["bindings"]["alias"] = OWNER
    command = import_module(f"{exchange.package}.command.exchange")
    matcher = SimpleNamespace(send=AsyncMock(), finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: "alias")

    async def run():
        state = await _selected_account_state(exchange, actor="alias", command_2="-")
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

    assert "绑定关系发生变化" in matcher.finish.await_args.args[0]
    assert len(stored["plans"]) == 2


def test_virtual_exchange_without_game_records_prompts_player_to_bind_game(exchange, monkeypatch):
    plan = _plan(exchange, FUTURE, virtual=True)
    records = _records(exchange, plan)
    command = import_module(f"{exchange.package}.command.exchange")
    monkeypatch.setattr(command, "get_good_list", AsyncMock(return_value=(True, [plan.good])))
    monkeypatch.setattr(command, "get_bound_game_roles", AsyncMock(return_value=(import_module(f"{exchange.package}.model.common").BaseApiStatus(success=True), [])))
    matcher = SimpleNamespace(finish=AsyncMock(side_effect=FinishedException))
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        state = await _selected_account_state(exchange, command_2="+")
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-2].call(
                event=event, matcher=matcher, state=state, good_id=GOODS_ID,
            )

    asyncio.run(_with_database(exchange, records, run))

    assert "还没有绑定游戏账号" in matcher.finish.await_args.args[0]


@pytest.mark.parametrize("virtual", [True, False])
def test_exchange_uses_game_role_uid_or_shipping_address_after_account_selection(exchange, monkeypatch, virtual):
    plan = _plan(exchange, FUTURE, virtual=virtual)
    plan.good.game_biz = "hk4e_cn"
    if not virtual:
        common = import_module(f"{exchange.package}.model.common")
        plan.account.address = common.Address(
            connect_areacode="+86", connect_mobile="10000000000", province_name="省",
            city_name="市", county_name="区", addr_ext="街道", connect_name="收件人", id="678",
        )
    records = _records(exchange, plan)
    records["plans"].clear()
    command = import_module(f"{exchange.package}.command.exchange")
    monkeypatch.setattr(command, "get_good_list", AsyncMock(return_value=(True, [plan.good])))
    common = import_module(f"{exchange.package}.model.common")
    monkeypatch.setattr(command, "get_bound_game_roles", AsyncMock(
        return_value=(common.BaseApiStatus(success=True), [plan.game_record]),
    ))
    matcher = command.myb_exchange_plan()
    matcher.send = AsyncMock()
    matcher.finish = AsyncMock(side_effect=FinishedException)
    event = SimpleNamespace(get_user_id=lambda: OWNER)

    async def run():
        matcher.state.update(await _selected_account_state(exchange, command_2="+"))
        await command.myb_exchange_plan.handlers[-2].call(
            event=event, matcher=matcher, state=matcher.state, good_id=GOODS_ID,
        )
        if virtual:
            menu = matcher.send.await_args.args[0]
            assert "1. 测试玩家" in menu and "1001" not in menu
            game_uid = "1"
        else:
            prefilled = matcher.get_arg("game_uid")
            assert prefilled is not None
            game_uid = str(prefilled)
        with pytest.raises(FinishedException):
            await command.myb_exchange_plan.handlers[-1].call(
                event=event, matcher=matcher, state=matcher.state, game_uid=game_uid,
            )
        return await exchange.storage.Repository.load()

    stored = asyncio.run(_with_database(exchange, records, run))

    saved, = stored["plans"].values()
    assert saved["bbs_uid"] == UID
    if virtual:
        assert saved["game_record"]["game_role_id"] == "1001"
    else:
        assert saved["address"]["id"] == "678"



def test_parallel_owners_with_same_mihoyo_account_submit_only_once(exchange, monkeypatch):
    plan = _plan(exchange, FUTURE)
    records = _records(exchange, plan)
    records["users"]["other"] = json.loads(exchange.model.UserData().json(exclude={"accounts", "exchange_plans"}))
    records["accounts"][("other", UID)] = records["accounts"][(OWNER, UID)]
    plan_id = _old_id(FUTURE)
    records["plans"][("other", plan_id)] = records["plans"][(OWNER, plan_id)]
    common = import_module(f"{exchange.package}.model.common")
    models = import_module(f"{exchange.package}.storage.models")
    calls = []

    def unknown_receipt(scheduled):
        calls.append(scheduled.scheduled_at)
        return common.ExchangeStatus(network_error=True), None

    async def run():
        monkeypatch.setattr(exchange.jobs, "good_exchange_sync", unknown_receipt)
        monkeypatch.setattr(exchange.jobs.time, "time", lambda: FUTURE)
        await asyncio.gather(
            exchange.jobs.execute_exchange_job(OWNER, plan_id),
            exchange.jobs.execute_exchange_job("other", plan_id),
        )
        row = await models.MystoolExchangeExecution.get()
        assert row.outcome == "unknown"
        assert not (await exchange.storage.Repository.load())["plans"]

    asyncio.run(_with_database(exchange, records, run))
    assert calls == [FUTURE]


@pytest.mark.parametrize("http_status,payload,expected", [
    (200, {"retcode": 0, "message": "OK", "data": {"order_id": "fixture"}}, True),
    (502, {"retcode": 0, "message": "OK", "data": {"order_id": "fixture"}}, None),
    (200, {"retcode": -1, "message": "OK", "data": {}}, None),
    (200, {"retcode": -464, "message": "未知错误", "data": None}, None),
])
def test_exchange_receipt_requires_confirmed_business_result(exchange, monkeypatch, http_status, payload, expected):
    import httpx

    api = import_module(f"{exchange.package}.api.common")
    client = httpx.Client
    attempts = []

    def respond(request):
        attempts.append(request.method)
        return httpx.Response(http_status, json=payload)

    monkeypatch.setattr(httpx, "Client", lambda: client(transport=httpx.MockTransport(respond)))
    status, outcome = api.good_exchange_sync(_plan(exchange, FUTURE))
    assert attempts == ["POST"]
    assert (outcome.result if outcome is not None else None) is expected
    assert bool(status) == bool(expected)



def test_pending_exchange_survives_reopening_database(exchange, monkeypatch, tmp_path):
    plan = _plan(exchange, FUTURE)
    models = import_module(f"{exchange.package}.storage.models")
    db_url = f"sqlite://{(tmp_path / 'mystool.sqlite3').as_posix()}"
    modules = {"models": [f"{exchange.package}.storage.models"]}
    plan_id = _old_id(FUTURE)

    async def run():
        await Tortoise.init(db_url=db_url, modules=modules)
        try:
            await Tortoise.generate_schemas()
            await exchange.storage.initialize_storage()
            repository = exchange.storage.Repository
            await repository.commit(await repository.load(), _records(exchange, plan))
            monkeypatch.setattr(exchange.jobs.time, "time", lambda: FUTURE)
            _, state = await exchange.jobs._claim(OWNER, plan_id, None)
            assert state == "claimed"
        finally:
            await Tortoise.close_connections()

        await Tortoise.init(db_url=db_url, modules=modules)
        try:
            await exchange.storage.initialize_storage()
            await exchange.jobs.execute_exchange_job(OWNER, plan_id)
            await exchange.jobs.restore_exchange_jobs()
            assert not exchange.jobs.scheduler.get_jobs()
            pending = await models.MystoolExchangeExecution.get()
            assert pending.outcome == "unknown"
            assert not (await repository.load())["plans"]
        finally:
            await Tortoise.close_connections()

    asyncio.run(run())
