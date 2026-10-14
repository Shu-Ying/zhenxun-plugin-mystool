import json
from pathlib import Path

from nonebot.log import logger

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

MODULE = "mystool"
DEFAULT_COMMAND_PREFIX = "米游社"


def group_parser(model_type, baseline: dict, use_prefix: bool):
    def parse(value):
        if not isinstance(value, dict):
            raise ValueError("mystool 配置组必须为字典")
        model = model_type.parse_obj({**baseline, **value})
        if use_prefix:
            model.command_start = model.command_start.strip() or DEFAULT_COMMAND_PREFIX
        return json.loads(model.json())

    return parse


def load_config(config_type, legacy_path: Path):
    defaults = json.loads(config_type().json())
    legacy = {}
    source = legacy_path
    if not source.exists():
        source = legacy_path.parent / "plugin_data.json"
    if source.exists():
        legacy = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(legacy, dict):
            raise ValueError("mystool 旧配置顶层必须为字典")

    registrations = []
    resolved = {}
    descriptions = {
        "preference": "米游社运行偏好；命令前缀为空时使用米游社；修改后重启生效",
        "good_list_image_config": "米游社商品图片生成配置；修改后重启生效",
    }
    for group, default in defaults.items():
        previous = legacy.get(group, {})
        if not isinstance(previous, dict):
            raise ValueError(f"mystool 旧配置 {group} 必须为字典")
        fallback = {**default, **previous}
        existing = Config.get(MODULE).configs.get(group.upper())
        if existing is not None:
            if not isinstance(existing.value, dict):
                raise ValueError(f"mystool 配置 {group} 必须为字典")
            fallback.update(existing.value)
        registration = RegisterConfig(
            module=MODULE,
            key=group.upper(),
            value=fallback,
            default_value=default,
            help=descriptions[group],
            type=dict,
        )
        Config.add_plugin_config(
            MODULE,
            registration.key,
            fallback,
            default_value=default,
            help=registration.help,
            type=dict,
            _override=True,
        )
        resolved[group] = Config.get_config(MODULE, registration.key, build_model=False)
        registrations.append(registration)

    try:
        config = config_type.parse_obj(resolved)
        prefix = config.preference.command_start.strip()
        config.preference.command_start = prefix or DEFAULT_COMMAND_PREFIX
    except Exception:
        logger.error("mystool 配置校验失败，请检查 mystool 配置组")
        raise

    normalized = json.loads(config.json())
    for registration in registrations:
        group = registration.key.lower()
        Config.set_config(MODULE, registration.key, normalized[group])
        registration.value = normalized[group]
        registration.arg_parser = group_parser(
            config_type.__fields__[group].type_,
            normalized[group],
            group == "preference",
        )
        Config.add_plugin_config(
            MODULE,
            registration.key,
            registration.value,
            default_value=registration.default_value,
            help=registration.help,
            type=dict,
            arg_parser=registration.arg_parser,
        )
    Config.get(MODULE).name = "米游社小助手"
    Config.save()
    if source.exists():
        logger.info("mystool 已读取旧配置；已有真寻配置优先，旧文件保持原样")
    return config, registrations
