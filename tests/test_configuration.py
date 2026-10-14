from unittest.mock import patch

import pytest

from .conftest import config_manager


def test_individual_configuration_and_yaml_roundtrip(runtime, tmp_path):
    from zhenxun_plugin_mystool.model.config import PluginConfig
    from zhenxun_plugin_mystool.configuration import registry
    from zhenxun_plugin_mystool.configuration.keys import MODULE
    from zhenxun.builtin_plugins.init import init_config

    manager = config_manager(tmp_path)
    manager.add_plugin_config(MODULE, "请求超时秒", 31)
    manager.add_plugin_config(MODULE, "命令前缀", "/custom")
    manager.add_plugin_config(MODULE, "每日任务时间", "03:45")
    manager.add_plugin_config(MODULE, "商品预览图尺寸", [300, 400])
    with patch.object(registry, "Config", manager):
        config, entries = registry.load_config(PluginConfig)
    assert config.preference.timeout == 31
    assert config.preference.command_start == "/custom"
    assert config.preference.plan_time == "03:45"
    assert config.good_list_image_config.ICON_SIZE == (300, 400)
    assert len(entries) > 20
    assert all(item.help and item.default_value == item.value for item in entries)
    assert all(
        item.key not in {"PREFERENCE", "GOOD_LIST_IMAGE_CONFIG"} for item in entries
    )
    registered = [f"{MODULE}:{item.key}".lower() for item in entries]
    with (
        patch.object(init_config, "Config", manager),
        patch.object(init_config, "DATA_PATH", tmp_path),
        patch.object(init_config, "SIMPLE_CONFIG_FILE", tmp_path / "config.yaml"),
    ):
        init_config._generate_simple_config(registered)
    reloaded = config_manager(tmp_path)
    with patch.object(registry, "Config", reloaded):
        restored, _ = registry.load_config(PluginConfig)
    assert restored.preference.timeout == 31
    assert restored.preference.command_start == "/custom"
    assert restored.preference.plan_time == "03:45"
    assert restored.good_list_image_config.ICON_SIZE == (300, 400)


def test_host_defaults_and_explicit_overrides(runtime, tmp_path):
    from zhenxun_plugin_mystool.model.config import PluginConfig
    from zhenxun_plugin_mystool.configuration import registry
    from zhenxun_plugin_mystool.configuration.keys import MODULE

    manager = config_manager(tmp_path)
    with patch.object(registry, "Config", manager):
        config, entries = registry.load_config(PluginConfig)
    assert config.preference.resin_interval == 720
    removed = {"启用黑名单", "黑名单", "启用白名单", "白名单",
               "启用管理员名单", "管理员名单", "自动接受好友申请", "好友欢迎语"}
    assert not removed.intersection(item.key for item in entries)
    manager.set_config(MODULE, "便笺检查间隔分钟", 30)
    with patch.object(registry, "Config", manager):
        config, _ = registry.load_config(PluginConfig)
    assert config.preference.resin_interval == 30


@pytest.mark.parametrize("value", ["", "mys"])
def test_prefix_defaults_and_types(runtime, tmp_path, value):
    from zhenxun_plugin_mystool.model.config import PluginConfig
    from zhenxun_plugin_mystool.configuration import registry
    from zhenxun_plugin_mystool.configuration.keys import MODULE

    manager = config_manager(tmp_path)
    manager.add_plugin_config(MODULE, "命令前缀", value)
    manager.add_plugin_config(MODULE, "请求超时秒", "29")
    manager.add_plugin_config(MODULE, "商品图片宽度", 1234)
    with patch.object(registry, "Config", manager):
        config, _ = registry.load_config(PluginConfig)
    assert config.preference.command_start == (value or "米游社")
    assert config.preference.timeout == 29
    assert config.good_list_image_config.WIDTH == 1234


@pytest.mark.parametrize(
    "key,value",
    [("请求超时秒", "bad"), ("每日任务时间", "25:61"), ("便笺检查间隔分钟", 0)],
)
def test_invalid_configuration_is_rejected(runtime, tmp_path, key, value):
    from zhenxun_plugin_mystool.model.config import PluginConfig
    from zhenxun_plugin_mystool.configuration import registry
    from zhenxun_plugin_mystool.configuration.keys import MODULE

    manager = config_manager(tmp_path)
    manager.add_plugin_config(MODULE, key, value)
    with (
        patch.object(registry, "Config", manager),
        pytest.raises((ValueError, TypeError)),
    ):
        registry.load_config(PluginConfig)
