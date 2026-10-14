import importlib
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.parametrize("level", ["info", "debug", "warning", "error", "success"])
def test_log_levels_are_forwarded_to_host(runtime, level):
    from zhenxun_plugin_mystool import logging as plugin_log

    with patch.object(plugin_log._host_logger, level) as log:
        getattr(plugin_log.logger, level)("test-message")
    log.assert_called_once_with("test-message", "米游社")


def test_exception_context_uses_host_error_and_no_plugin_sink(runtime):
    from zhenxun_plugin_mystool import logging as plugin_log
    from zhenxun.services import log as host_log

    with patch.object(host_log.logger_, "add") as add:
        importlib.reload(plugin_log)
    add.assert_not_called()
    failure = ValueError("test-error")
    with patch.object(plugin_log._host_logger, "error") as log:
        try:
            raise failure
        except ValueError:
            plugin_log.logger.exception("operation failed")
    log.assert_called_once_with("operation failed", "米游社", e=failure)


@pytest.mark.parametrize("code", [-2109, -2105])
def test_explicit_unavailable_goods_status(runtime, code):
    from zhenxun_plugin_mystool.api import common

    response = SimpleNamespace(
        json=lambda: {"retcode": code, "message": "unavailable", "data": {}}
    )
    client = MagicMock()
    client.__aenter__.return_value.get = AsyncMock(return_value=response)
    with patch.object(common.httpx, "AsyncClient", return_value=client):
        status, good = runtime.run(common.get_good_detail("test-good", retry=False))
    assert not status and status.good_not_existed and good is None


def test_image_cache_creation_does_not_depend_on_log_initialization(runtime, tmp_path):
    from zhenxun_plugin_mystool.command import exchange

    directory = tmp_path / "new" / "images"
    font = tmp_path / "existing-font.otf"
    font.write_bytes(b"font")
    goods = [SimpleNamespace(time_end=False, time_limited=True)]
    loop = asyncio.new_event_loop()
    try:
        with (
            patch.object(
                exchange.plugin_config.good_list_image_config, "SAVE_PATH", directory
            ),
            patch.object(
                exchange.plugin_config.good_list_image_config, "FONT_PATH", font
            ),
            patch.object(
                exchange, "get_good_list", AsyncMock(return_value=(True, goods))
            ),
            patch.object(
                exchange,
                "game_list_to_image",
                AsyncMock(return_value=b"generated-image"),
            ),
            patch.object(exchange.asyncio, "new_event_loop", return_value=loop),
        ):
            assert exchange.image_process("hk4e")
    finally:
        loop.close()
    files = list(directory.glob("*.jpg"))
    assert len(files) == 1 and files[0].read_bytes() == b"generated-image"
