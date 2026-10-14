import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture(autouse=True)
def clean_database(runtime):
    from zhenxun_plugin_mystool.storage.models import MystoolUser

    runtime.run(MystoolUser.all().delete())


def example_user():
    from zhenxun_plugin_mystool.model.data import (
        UserData,
        UserAccount,
        BBSCookies,
        ExchangePlan,
    )
    from zhenxun_plugin_mystool.model.common import Good

    account = UserAccount(
        cookies=BBSCookies(stuid="123", stoken="test-token", cookie_token="test-cookie")
    )
    good = Good(
        type=0,
        next_time=9999999999,
        goods_id="test-good",
        price=10,
        icon="https://example.invalid/image",
        account_exchange_num=0,
        account_cycle_limit=1,
        account_cycle_type="forever",
    )
    return UserData(
        accounts={"123": account},
        exchange_plans=[ExchangePlan(good=good, account=account)],
    )


async def seed(user_ids=("owner",)):
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async with PluginDataManager.session():
        for uid in user_ids:
            PluginDataManager.plugin_data.users[uid] = example_user()
        await PluginDataManager.save()


def test_database_roundtrip_and_bindings(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.models import (
        MystoolUser,
        MystoolAccount,
        MystoolPlan,
        MystoolBinding,
    )

    async def check():
        await seed()
        async with PluginDataManager.session():
            data = PluginDataManager.plugin_data
            data.do_user_bind("alias", "owner")
            data.users["owner"].enable_notice = False
            await PluginDataManager.save()
        async with PluginDataManager.session():
            data = PluginDataManager.plugin_data
            assert data.users["owner"] is data.users["alias"]
            assert data.users["owner"].enable_notice is False
            account = data.users["owner"].accounts["123"]
            assert account.cookies.cookie_token == "test-cookie"
            assert next(iter(data.users["owner"].exchange_plans)).account is account
        assert await MystoolUser.all().count() == 1
        assert await MystoolAccount.all().count() == 1
        assert await MystoolPlan.all().count() == 1
        assert await MystoolBinding.all().count() == 1
        from tortoise import Tortoise

        await Tortoise.close_connections()
        async with PluginDataManager.session():
            assert (
                PluginDataManager.plugin_data.users["owner"]
                .accounts["123"]
                .cookies.cookie_token
                == "test-cookie"
            )
        assert not (
            runtime.workspace / "data/nonebot-plugin-mystool/dataV2.json"
        ).exists()

    runtime.run(check())


def test_child_tasks_do_not_inherit_mutable_session(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async def check():
        await seed()
        async with PluginDataManager.session():
            PluginDataManager.plugin_data.users["owner"].enable_notice = False

            async def child():
                async with PluginDataManager.session():
                    assert (
                        PluginDataManager.plugin_data.users["owner"].enable_notice
                        is True
                    )

            await asyncio.create_task(child())

    runtime.run(check())


def test_account_setting_handler_commits_before_finish(runtime):
    from nonebot.exception import FinishedException
    from zhenxun_plugin_mystool.command.setting import account_setting
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async def check():
        await seed()
        event = SimpleNamespace(get_user_id=lambda: "owner")
        with (
            patch.object(account_setting, "send", AsyncMock()),
            pytest.raises(FinishedException),
        ):
            await account_setting.handlers[-1].call(
                event=event,
                state={"bbs_uid": "123", "setting_item": "setting_notice_value_op"},
                setting_value="100",
            )
        async with PluginDataManager.session():
            assert (
                PluginDataManager.plugin_data.users["owner"]
                .accounts["123"]
                .user_resin_threshold
                == 100
            )

    runtime.run(check())


def test_database_lifecycle_order_and_model_registration(runtime):
    from zhenxun.utils.manager.priority_manager import PriorityLifecycle
    from zhenxun.utils.enum import PriorityLifecycleType
    from zhenxun.services.db_context.config import db_model
    from zhenxun_plugin_mystool.storage.session import initialize_storage
    from zhenxun_plugin_mystool.services.exchange_jobs import restore_exchange_jobs

    hooks = PriorityLifecycle._data[PriorityLifecycleType.STARTUP]
    assert initialize_storage in hooks[2]
    assert restore_exchange_jobs in hooks[3]
    assert any(hook.__module__ == "zhenxun.services.db_context" for hook in hooks[1])
    assert "zhenxun_plugin_mystool.storage.models" in db_model.models


def test_transaction_failure_rolls_back_database_and_session(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.repository import Repository

    async def check():
        await seed()
        original = await Repository.load()
        apply = Repository.apply

        async def fail_after_write(latest, merged, connection):
            await apply(latest, merged, connection)
            raise RuntimeError("injected transaction failure")

        async with PluginDataManager.session():
            PluginDataManager.plugin_data.users["owner"].enable_notice = False
            PluginDataManager.plugin_data.do_user_bind("alias", "owner")
            with (
                patch.object(Repository, "apply", fail_after_write),
                pytest.raises(RuntimeError),
            ):
                await PluginDataManager.save()
            assert PluginDataManager.plugin_data.users["owner"].enable_notice is True
            assert not PluginDataManager.plugin_data.user_bind
        assert await Repository.load() == original

    runtime.run(check())


def test_concurrent_sessions_preserve_independent_users(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async def check():
        await seed(("first", "second"))
        barrier = asyncio.Event()
        loaded = 0

        async def change(uid):
            nonlocal loaded
            async with PluginDataManager.session():
                loaded += 1
                if loaded == 2:
                    barrier.set()
                await barrier.wait()
                PluginDataManager.plugin_data.users[uid].enable_notice = False
                await PluginDataManager.save()

        await asyncio.gather(change("first"), change("second"))
        async with PluginDataManager.session():
            assert all(
                not user.enable_notice
                for user in PluginDataManager.plugin_data.users.values()
            )

    runtime.run(check())


def test_conflicting_updates_do_not_overwrite(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.repository import (
        Repository,
        ConcurrentUpdateError,
    )
    from zhenxun_plugin_mystool.storage.codec import encode, decode

    async def check():
        await seed()
        base = await Repository.load()
        first, second = decode(base), decode(base)
        first.users["owner"].accounts["123"].user_resin_threshold = 100
        second.users["owner"].accounts["123"].user_resin_threshold = 150
        await Repository.commit(base, encode(first))
        with pytest.raises(ConcurrentUpdateError):
            await Repository.commit(base, encode(second))
        async with PluginDataManager.session():
            assert (
                PluginDataManager.plugin_data.users["owner"]
                .accounts["123"]
                .user_resin_threshold
                == 100
            )

    runtime.run(check())


def test_restart_restores_jobs_from_database(runtime):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async def check():
        await seed()
        with (
            patch.object(
                exchange_jobs,
                "get_good_detail",
                AsyncMock(
                    return_value=(True, next(iter(example_user().exchange_plans)).good)
                ),
            ),
            patch.object(exchange_jobs, "schedule_plan") as schedule,
        ):
            await exchange_jobs.restore_exchange_jobs()
        schedule.assert_called_once()
        assert schedule.call_args.args[0] == "owner"
        async with PluginDataManager.session():
            assert len(PluginDataManager.plugin_data.users["owner"].exchange_plans) == 1

    runtime.run(check())


def test_exchange_callback_awaits_commit_before_notification(runtime):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.codec import plan_key
    from zhenxun_plugin_mystool.model.config import plugin_config

    async def check():
        await seed()
        async with PluginDataManager.session():
            identity = plan_key(
                next(iter(PluginDataManager.plugin_data.users["owner"].exchange_plans))
            )
        key = ("owner", identity)
        exchange_jobs._jobs["test-job"] = (key, 0)
        exchange_jobs._results[key] = {}
        event = SimpleNamespace(
            job_id="test-job",
            retval=(True, SimpleNamespace(result=True)),
            exception=None,
        )

        async def sent(*args):
            async with PluginDataManager.session():
                assert not PluginDataManager.plugin_data.users["owner"].exchange_plans

        with (
            patch.object(plugin_config.preference, "exchange_thread_count", 1),
            patch.object(
                exchange_jobs, "send_private_msg", AsyncMock(side_effect=sent)
            ) as send,
        ):
            await exchange_jobs.process_exchange_result(event)
        send.assert_awaited_once()

    runtime.run(check())


def test_exchange_commit_failure_has_no_success_notification(runtime):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.codec import plan_key
    from zhenxun_plugin_mystool.storage.repository import Repository
    from zhenxun_plugin_mystool.model.config import plugin_config

    async def check():
        await seed()
        async with PluginDataManager.session():
            identity = plan_key(
                next(iter(PluginDataManager.plugin_data.users["owner"].exchange_plans))
            )
        key = ("owner", identity)
        exchange_jobs._jobs["failed-job"] = (key, 0)
        exchange_jobs._results[key] = {}
        event = SimpleNamespace(
            job_id="failed-job",
            retval=(True, SimpleNamespace(result=True)),
            exception=None,
        )
        with (
            patch.object(plugin_config.preference, "exchange_thread_count", 1),
            patch.object(
                Repository,
                "commit",
                AsyncMock(side_effect=RuntimeError("commit failed")),
            ),
            patch.object(exchange_jobs, "send_private_msg", AsyncMock()) as send,
        ):
            with pytest.raises(RuntimeError):
                await exchange_jobs.process_exchange_result(event)
        send.assert_not_awaited()
        async with PluginDataManager.session():
            assert PluginDataManager.plugin_data.users["owner"].exchange_plans

    runtime.run(check())


def test_deleted_plan_is_not_exchanged(runtime):
    from zhenxun_plugin_mystool.services import exchange_jobs

    async def check():
        with patch.object(exchange_jobs, "good_exchange_sync") as exchange:
            assert await exchange_jobs.execute_exchange_job("missing", "plan") == (
                False,
                None,
            )
        exchange.assert_not_called()

    runtime.run(check())


def test_nested_settings_are_saved_and_rollback_remains_isolated(runtime):
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.storage.repository import Repository
    from zhenxun_plugin_mystool.model.data import UserData

    async def check():
        async with PluginDataManager.session():
            PluginDataManager.plugin_data.users["owner"] = UserData(
                geetest_params={"nested": {"values": [1]}}
            )
            await PluginDataManager.save()
        async with PluginDataManager.session() as session:
            session.data.users["owner"].geetest_params["nested"]["values"][0] = 2
            assert session.base["users"]["owner"]["geetest_params"]["nested"][
                "values"
            ] == [1]
            await PluginDataManager.save()
        async with PluginDataManager.session() as session:
            assert session.data.users["owner"].geetest_params["nested"]["values"] == [2]
            session.data.users["owner"].geetest_params["nested"]["values"][0] = 3
            with (
                patch.object(
                    Repository, "commit", AsyncMock(side_effect=RuntimeError("fail"))
                ),
                pytest.raises(RuntimeError),
            ):
                await PluginDataManager.save()
            assert session.data.users["owner"].geetest_params["nested"]["values"] == [2]
            session.data.users["owner"].geetest_params["nested"]["values"][0] = 4
            assert session.base["users"]["owner"]["geetest_params"]["nested"][
                "values"
            ] == [2]
            await PluginDataManager.save()
        async with PluginDataManager.session():
            assert PluginDataManager.plugin_data.users["owner"].geetest_params[
                "nested"
            ]["values"] == [4]

    runtime.run(check())


@pytest.mark.parametrize("error_kind", ["network_error", "incorrect_return"])
def test_temporary_lookup_failure_preserves_and_restores_plan(runtime, error_kind):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.model.common import GetGoodDetailStatus

    async def check():
        await seed()
        with (
            patch.object(
                exchange_jobs,
                "get_good_detail",
                AsyncMock(
                    return_value=(GetGoodDetailStatus(**{error_kind: True}), None)
                ),
            ),
            patch.object(exchange_jobs, "schedule_plan") as schedule,
        ):
            await exchange_jobs.restore_exchange_jobs()
        schedule.assert_called_once()
        async with PluginDataManager.session():
            assert len(PluginDataManager.plugin_data.users["owner"].exchange_plans) == 1

    runtime.run(check())


@pytest.mark.parametrize("reason", ["not_found", "expired"])
def test_confirmed_invalid_plans_are_deleted(runtime, reason):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.model.common import GetGoodDetailStatus

    async def check():
        await seed()
        good = next(iter(example_user().exchange_plans)).good
        good.next_time = 1
        result = (
            (GetGoodDetailStatus(good_not_existed=True), None)
            if reason == "not_found"
            else (GetGoodDetailStatus(success=True), good)
        )
        with (
            patch.object(
                exchange_jobs, "get_good_detail", AsyncMock(return_value=result)
            ),
            patch.object(exchange_jobs, "schedule_plan") as schedule,
        ):
            await exchange_jobs.restore_exchange_jobs()
        schedule.assert_not_called()
        async with PluginDataManager.session():
            assert not PluginDataManager.plugin_data.users["owner"].exchange_plans

    runtime.run(check())


def test_updated_plan_time_is_persisted_and_scheduled(runtime):
    from zhenxun_plugin_mystool.services import exchange_jobs
    from zhenxun_plugin_mystool.storage import PluginDataManager
    from zhenxun_plugin_mystool.model.common import GetGoodDetailStatus

    async def check():
        await seed()
        good = next(iter(example_user().exchange_plans)).good
        good.next_time = 8888888888
        lookup = AsyncMock(return_value=(GetGoodDetailStatus(success=True), good))
        with (
            patch.object(exchange_jobs, "get_good_detail", lookup),
            patch.object(exchange_jobs, "schedule_plan") as schedule,
        ):
            await exchange_jobs.restore_exchange_jobs()
        lookup.assert_awaited_once_with("test-good")
        assert schedule.call_args.args[1].good.time == 8888888888
        async with PluginDataManager.session():
            assert (
                next(
                    iter(PluginDataManager.plugin_data.users["owner"].exchange_plans)
                ).good.time
                == 8888888888
            )

    runtime.run(check())
