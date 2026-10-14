import asyncio
import importlib
from importlib.abc import MetaPathFinder
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

import nonebot
import pytest


class BlockQQAdapter(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "nonebot.adapters.qq" or fullname.startswith(
            "nonebot.adapters.qq."
        ):
            raise ModuleNotFoundError(
                "QQ official adapter is unavailable", name=fullname
            )


def config_manager(directory):
    from zhenxun.configs.utils import ConfigsManager

    with patch("zhenxun.configs.utils.DATA_PATH", directory):
        return ConfigsManager(directory / "registered.yaml")


@pytest.fixture(scope="session")
def runtime(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    host = Path(os.environ.get("ZHENXUN_ROOT", root.parent / "zhenxun_bot"))
    assert (host / "zhenxun").is_dir(), "请设置 ZHENXUN_ROOT 为真寻源码目录"
    workspace = tmp_path_factory.mktemp("mystool-host")
    shutil.copytree(
        host / "zhenxun",
        workspace / "zhenxun",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    original = Path.cwd()
    blocker = BlockQQAdapter()
    sys.meta_path.insert(0, blocker)
    sys.path[:0] = [str(workspace), str(root / "src")]
    os.chdir(workspace)
    nonebot.init(
        driver="~fastapi+~httpx+~websockets",
        command_start={""},
        db_url="sqlite:data/zhenxun.db",
        superusers=set(),
    )
    from nonebot.adapters.onebot.v11 import Adapter

    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    importlib.import_module("zhenxun.services")
    loaders = workspace / "external_plugins"
    loaders.mkdir()
    shutil.copyfile(root / "zhenxun_loader.py", loaders / "mystool_loader.py")
    nonebot.load_plugins(str(loaders))
    plugin = nonebot.get_plugin("zhenxun_plugin_mystool")
    assert plugin is not None
    from tortoise import Tortoise
    from zhenxun.services.db_context import get_config
    from zhenxun_plugin_mystool.storage.session import initialize_storage

    loop = asyncio.new_event_loop()
    run = loop.run_until_complete
    run(Tortoise.init(config=get_config()))
    run(Tortoise.generate_schemas())
    run(initialize_storage())
    yield SimpleNamespace(plugin=plugin, workspace=workspace, driver=driver, run=run)
    run(Tortoise.close_connections())
    loop.close()
    sys.meta_path.remove(blocker)
    os.chdir(original)
