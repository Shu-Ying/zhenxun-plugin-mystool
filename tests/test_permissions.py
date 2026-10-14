from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from .test_storage import seed


@pytest.mark.parametrize(
    "superuser,global_level,group_level,expected",
    [(True, 0, 0, True), (False, 1, 0, True), (False, 0, 9, False),
     (False, 0, 0, False), (False, -1, 9, False)],
)
def test_only_host_global_authority_manages_accounts(
    runtime, superuser, global_level, group_level, expected
):
    from zhenxun.models.level_user import LevelUser
    from zhenxun_plugin_mystool.services import permissions

    async def check():
        await LevelUser.set_level("permission-user", "", global_level)
        await LevelUser.set_level("permission-user", "group", group_level)
        event = SimpleNamespace(get_user_id=lambda: "permission-user", group_id="group")
        with patch.object(permissions, "SUPERUSER", AsyncMock(return_value=superuser)):
            assert await permissions.can_manage_accounts(object(), event) is expected
    runtime.run(check())


@pytest.mark.parametrize("command,perform", [
    ("manually_game_sign", "perform_game_sign"),
    ("manually_bbs_sign", "perform_bbs_sign"),
])
@pytest.mark.parametrize("target", ["456", "*"])
@pytest.mark.parametrize("allowed", [False, True])
def test_cross_user_commands_check_permission_before_account_actions(
    runtime, command, perform, target, allowed
):
    from nonebot.exception import FinishedException
    from zhenxun_plugin_mystool.command import plan
    from zhenxun_plugin_mystool.storage import PluginDataManager

    async def check():
        await seed(("permission-owner", "456"))
        matcher = getattr(plan, command)
        event = SimpleNamespace(get_user_id=lambda: "permission-owner")
        api = AsyncMock()
        with (
            patch.object(plan, "can_manage_accounts", AsyncMock(return_value=allowed)),
            patch.object(plan, perform, api),
            patch.object(matcher, "send", AsyncMock()),
        ):
            handler = matcher.handlers[0].call
            if allowed:
                await handler(bot=object(), event=event, matcher=matcher, command_arg=target)
                assert api.await_count >= 1
            else:
                with pytest.raises(FinishedException):
                    await handler(bot=object(), event=event, matcher=matcher, command_arg=target)
                api.assert_not_awaited()
        async with PluginDataManager.session():
            assert PluginDataManager.plugin_data.users["456"].accounts
    runtime.run(check())


def test_note_interval_and_host_owned_friend_requests(runtime):
    from nonebot_plugin_apscheduler import scheduler
    assert not any(m.type == "request" for m in runtime.plugin.matcher)
    assert scheduler.get_job("resin_check").trigger.interval.total_seconds() == 720 * 60


@pytest.mark.parametrize("command,perform", [
    ("manually_game_sign", "perform_game_sign"),
    ("manually_bbs_sign", "perform_bbs_sign"),
])
def test_self_service_does_not_require_admin(runtime, command, perform):
    from zhenxun_plugin_mystool.command import plan

    async def check():
        await seed(("permission-owner",))
        matcher = getattr(plan, command)
        with (
            patch.object(plan, "can_manage_accounts", AsyncMock(return_value=False)) as permission,
            patch.object(plan, perform, AsyncMock()) as api,
            patch.object(matcher, "send", AsyncMock()),
        ):
            await matcher.handlers[0].call(
                bot=object(), event=SimpleNamespace(get_user_id=lambda: "permission-owner"),
                matcher=matcher, command_arg="",
            )
            permission.assert_not_awaited()
            api.assert_awaited_once()
    runtime.run(check())
