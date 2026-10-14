import ast
import inspect
import json

from zhenxun.configs.config import Config
from zhenxun.configs.utils import RegisterConfig

from .keys import MODULE, DEFAULT_COMMAND_PREFIX, PREFERENCE_KEYS, IMAGE_KEYS


def field_help(model_type):
    body = ast.parse(inspect.getsource(model_type)).body[0].body
    result = {}
    for index, node in enumerate(body[:-1]):
        following = body[index + 1]
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(following, ast.Expr) and isinstance(
                following.value, ast.Constant
            ):
                if isinstance(following.value.value, str):
                    result[node.target.id] = following.value.value.strip()
    return result


def field_parser(model_type, field):
    def parse(value):
        model = model_type.parse_obj({field: value})
        if field == "command_start":
            model.command_start = model.command_start.strip() or DEFAULT_COMMAND_PREFIX
        return json.loads(model.json())[field]

    return parse


def load_config(config_type):
    defaults = config_type()
    groups = {}
    registrations = []
    for group, keys in (
        ("preference", PREFERENCE_KEYS),
        ("good_list_image_config", IMAGE_KEYS),
    ):
        model = getattr(defaults, group)
        values = json.loads(model.json())
        descriptions = field_help(type(model))
        if set(keys) != set(values):
            raise ValueError(f"{group} 配置字段未逐项注册")
        resolved = {}
        for field, key in keys.items():
            default = values[field]
            if field == "command_start":
                default = DEFAULT_COMMAND_PREFIX
            parser = field_parser(type(model), field)
            reg = RegisterConfig(
                key=key,
                value=default,
                default_value=default,
                help=descriptions.get(field, key),
                type=type(default) if default is not None else None,
                arg_parser=parser,
            )
            Config.add_plugin_config(
                MODULE,
                key,
                default,
                default_value=default,
                help=reg.help,
                type=reg.type,
                arg_parser=parser,
            )
            value = Config.get_config(MODULE, key, build_model=False)
            resolved[field] = parser(value)
            registrations.append(reg)
        groups[group] = resolved
    return config_type.parse_obj(groups), registrations
