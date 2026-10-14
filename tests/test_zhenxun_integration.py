import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import nonebot
import pytest


def test_loading_and_metadata(runtime):
    plugin = runtime.plugin
    assert plugin.name == "zhenxun_plugin_mystool"
    assert nonebot.get_plugin("nonebot_plugin_mystool") is None
    assert nonebot.get_plugin("nonebot_plugin_saa") is None
    assert "nonebot.adapters.qq" not in sys.modules
    assert plugin.metadata.supported_adapters == {"~onebot.v11"}
    assert plugin.metadata.extra["configs"]
    commands = plugin.metadata.extra["commands"]
    assert any(c["command"] == "米游社签到" for c in commands)
    assert plugin.metadata.extra["menu_type"] == "米游社"
    assert "米游社签到" in plugin.metadata.usage
    assert "私信响应" not in plugin.metadata.usage
    assert all("私信响应" not in c["command"] for c in commands)
    from zhenxun_plugin_mystool.model.config import plugin_config

    assert plugin_config.preference.command_start == "米游社"
    from zhenxun_plugin_mystool.model.common import data_path

    assert data_path.resolve() == runtime.workspace / "data" / "zhenxun_plugin_mystool"


def test_prefixed_command_rules_leave_host_commands_available(runtime):
    from nonebot.rule import CommandRule

    rules = [
        checker.call
        for matcher in runtime.plugin.matcher
        for checker in matcher.rule.checkers
        if isinstance(checker.call, CommandRule)
    ]
    assert rules
    for rule in rules:
        for command in rule.cmds:
            assert command[0].startswith("米游社")
        for original in ("签到", "帮助", "设置"):
            assert not asyncio.run(
                rule(cmd=(original,), cmd_arg=None, cmd_whitespace=None)
            )
        for removed in ("私信响应", "私聊响应", "请求响应"):
            assert not asyncio.run(
                rule(cmd=("米游社" + removed,), cmd_arg=None, cmd_whitespace=None)
            )
    assert any(
        asyncio.run(rule(cmd=("米游社签到",), cmd_arg=None, cmd_whitespace=None))
        for rule in rules
    )


def test_private_message_text_image_and_failure(runtime):
    from nonebot.adapters.onebot.v11 import Bot
    from zhenxun_plugin_mystool.utils import common
    from zhenxun.utils.message import MessageUtils

    bot = Bot(nonebot.get_adapters()["OneBot V11"], "test-bot")
    message = MessageUtils.build_message(["reward", b"image-bytes"])
    send = AsyncMock(return_value=object())
    with patch.object(common.PlatformUtils, "send_message", send):
        assert asyncio.run(common.send_private_msg("123", message, use=bot)) == (
            True,
            None,
        )
    assert send.call_args.kwargs["message"] is message
    assert message[0].text == "reward"
    assert message[1].raw == b"image-bytes"
    error = RuntimeError("delivery failed")
    with patch.object(
        common.PlatformUtils, "send_message", AsyncMock(side_effect=error)
    ):
        assert asyncio.run(common.send_private_msg("123", "hello", use=bot)) == (
            False,
            error,
        )
    with patch.object(
        common.PlatformUtils, "send_message", AsyncMock(return_value=None)
    ):
        assert asyncio.run(common.send_private_msg("123", "hello", use=bot))[0] is False
    with patch.object(nonebot, "get_bots", return_value={}):
        ok, error = asyncio.run(common.send_private_msg("123", "hello"))
        assert not ok and isinstance(error, RuntimeError)


def test_selected_unsupported_bot_is_not_replaced(runtime):
    from nonebot.adapters import Bot
    from zhenxun_plugin_mystool.utils import common

    bot = MagicMock(spec=Bot)
    with patch.object(nonebot, "get_bots") as all_bots:
        ok, error = asyncio.run(common.send_private_msg("123", "hello", use=bot))
    assert not ok and isinstance(error, RuntimeError)
    all_bots.assert_not_called()


def test_account_data_and_scheduler_contract(runtime):
    from zhenxun_plugin_mystool.model.data import (
        PluginData,
        UserData,
        UserAccount,
        BBSCookies,
        ExchangePlan,
    )
    from zhenxun_plugin_mystool.model.common import Good
    from zhenxun_plugin_mystool.model.config import plugin_config
    from nonebot_plugin_apscheduler import scheduler

    cookies = BBSCookies(stuid="123", stoken="test-token", cookie_token="test-cookie")
    account = UserAccount(cookies=cookies)
    good = Good(
        type=0,
        goods_id="test-good",
        price=10,
        icon="https://example.invalid/image",
        account_exchange_num=0,
        account_cycle_limit=1,
        account_cycle_type="forever",
    )
    plan = ExchangePlan(good=good, account=account)
    user = UserData(
        accounts={"123": account},
        enable_notice=False,
        exchange_plans=[plan],
    )
    data = PluginData(users={"user": user}, user_bind={"other": "user"})
    restored = PluginData.parse_raw(data.json())
    assert restored.user_bind == data.user_bind
    assert restored.users["user"].enable_notice is False
    assert restored.users["user"].accounts["123"].cookies.bbs_uid == "123"
    assert (
        next(iter(restored.users["user"].exchange_plans)).good.goods_id == "test-good"
    )
    assert restored.users["user"].accounts["123"].cookies.cookie_token == "test-cookie"
    job = scheduler.get_job("daily_schedule")
    assert job is not None
    hour, minute = plugin_config.preference.plan_time.split(":")
    assert str(job.trigger.fields[5]) == str(int(hour))
    assert str(job.trigger.fields[6]) == str(int(minute))
    assert scheduler.get_job("resin_check") is not None


def test_framework_configuration_and_plugin_registration(runtime):
    from zhenxun_plugin_mystool.model.config import plugin_config
    from zhenxun.builtin_plugins.init import init_config, init_plugin
    from zhenxun.configs.config import Config

    registered = []
    init_config._handle_config(runtime.plugin, registered)
    assert "zhenxun_plugin_mystool:命令前缀" in registered
    init_config._generate_simple_config(registered)
    Config.reload()
    assert (runtime.workspace / "data/config.yaml").is_file()
    assert Config.get_config("zhenxun_plugin_mystool", "命令前缀") == "米游社"
    plugins, limits = [], []
    asyncio.run(init_plugin._handle_setting(runtime.plugin, plugins, limits))
    assert plugins[0].module == "zhenxun_plugin_mystool"
    assert plugins[0].menu_type == "米游社"
    assert plugin_config.preference.command_start == "米游社"


def test_onebot_image_export_and_adapter_selection(runtime):
    from nonebot.adapters.onebot.v11 import Bot
    from zhenxun_plugin_mystool.utils import common
    from zhenxun.utils.message import MessageUtils

    adapter = nonebot.get_adapters()["OneBot V11"]
    bot = Bot(adapter, "12345")
    send = AsyncMock(return_value={"message_id": 1})
    with (
        patch.object(adapter, "bots", {bot.self_id: bot}),
        patch.object(Bot, "call_api", send),
    ):
        ok, error = asyncio.run(
            common.send_private_msg(
                "123",
                MessageUtils.build_message(["reward", b"image-bytes"]),
                use=adapter,
            )
        )
    assert ok, error
    assert send.call_args.args[0] == "send_msg"
    assert send.call_args.kwargs["message_type"] == "private"
    assert send.call_args.kwargs["user_id"] == 123
    message = send.call_args.kwargs["message"]
    assert message[0].data["text"] == "reward"
    assert any(segment.type == "image" for segment in message)


def test_verification_prompt_uses_configured_prefix(runtime):
    from zhenxun_plugin_mystool.command import plan
    from zhenxun_plugin_mystool.model.data import BBSCookies, UserAccount, UserData
    from zhenxun_plugin_mystool.utils.common import CommandBegin

    status = MagicMock(need_verify=True, login_expired=False)
    status.__bool__.return_value = False
    signer = SimpleNamespace(
        has_record=True,
        name="test-game",
        get_info=AsyncMock(return_value=(True, SimpleNamespace(is_sign=False))),
        sign=AsyncMock(return_value=(status, None)),
    )
    factory = MagicMock(return_value=signer, en_name="test")
    account = UserAccount(cookies=BBSCookies(stuid="123"), game_sign_games=["test"])
    user = UserData(accounts={"123": account})
    send = AsyncMock(return_value=(True, None))
    with (
        patch.object(plan, "get_game_record", AsyncMock(return_value=(True, []))),
        patch.object(plan.BaseGameSign, "available_game_signs", [factory]),
        patch.object(plan.asyncio, "sleep", AsyncMock()),
        patch.object(plan, "send_private_msg", send),
        patch.object(CommandBegin, "string", "/custom"),
    ):
        asyncio.run(plan.perform_game_sign(user, ["user"]))
    assert "『/custom账号设置』" in send.call_args.kwargs["message"]
    assert "『/账号设置』" not in send.call_args.kwargs["message"]


def test_private_send_failure_is_written_to_plugin_log(runtime, tmp_path):
    from nonebot.adapters.onebot.v11 import Bot
    from zhenxun_plugin_mystool.utils import common

    bot = Bot(nonebot.get_adapters()["OneBot V11"], "test-bot")
    path = tmp_path / "mystool.log"
    sink_ids = []
    add_sink = common.logger.add

    def capture_sink(*args, **kwargs):
        sink_id = add_sink(*args, **kwargs)
        sink_ids.append(sink_id)
        return sink_id

    try:
        with (
            patch.object(common.plugin_config.preference, "log_path", path),
            patch.object(common.plugin_config.preference, "log_head", ""),
            patch.object(common.logger, "add", side_effect=capture_sink),
            patch.object(
                common.PlatformUtils,
                "send_message",
                AsyncMock(side_effect=RuntimeError("test-failure")),
            ),
        ):
            common.set_logger(common.logger)
            assert not asyncio.run(common.send_private_msg("123", "hello", use=bot))[0]
        assert "发送私信失败" in path.read_text(encoding="utf-8")
    finally:
        for sink_id in sink_ids:
            common.logger.remove(sink_id)


@pytest.mark.parametrize("prefix", ["米游社", "/custom"])
def test_friend_welcome_uses_plugin_help_prefix(runtime, prefix):
    from nonebot.adapters.onebot.v11 import Bot, FriendRequestEvent
    from zhenxun_plugin_mystool.command import user_check
    from zhenxun_plugin_mystool.utils.common import CommandBegin

    bot = Bot(nonebot.get_adapters()["OneBot V11"], "test-bot")
    event = FriendRequestEvent(
        time=1,
        self_id=123,
        post_type="request",
        request_type="friend",
        user_id=456,
        comment="",
        flag="test-request",
    )
    api = AsyncMock()
    handler = user_check.friendRequest.handlers[0].call
    with (
        patch.object(Bot, "call_api", api),
        patch.object(user_check.asyncio, "sleep", AsyncMock()),
        patch.object(CommandBegin, "string", prefix),
        patch.object(user_check.plugin_config.preference, "add_friend_accept", True),
        patch.object(user_check.plugin_config.preference, "add_friend_welcome", True),
    ):
        asyncio.run(handler(bot, event))
    assert api.await_args_list[0].args == ("set_friend_add_request",)
    assert api.await_args_list[1].args == ("send_private_msg",)
    assert f"『{prefix}帮助』" in api.await_args_list[1].kwargs["message"]
