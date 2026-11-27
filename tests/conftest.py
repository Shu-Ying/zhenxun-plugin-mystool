import os
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def mystool(tmp_path_factory):
    """Load the shipped plugin with a host, without the real bot's config or database."""
    plugin_root = Path(__file__).resolve().parents[1]
    outer_root = plugin_root.parents[2]
    host_root = Path(os.environ.get("ZHENXUN_ROOT", outer_root)).resolve()
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(host_root))
        patch.syspath_prepend(str(plugin_root / "src"))
        patch.chdir(tmp_path_factory.mktemp("mystool-host"))

        import nonebot
        from nonebot.adapters.onebot.v11 import Adapter

        nonebot.init(
            driver="~fastapi", command_start={""}, apscheduler_autostart=False,
            db_url="sqlite://:memory:", log_level="WARNING",
        )
        nonebot.get_driver().register_adapter(Adapter)
        nonebot.require("nonebot_plugin_uninfo")
        plugin = nonebot.load_plugin("zhenxun_plugin_mystool")
        assert plugin is not None, "Mystool failed to load in the isolated host"
        yield plugin.module
