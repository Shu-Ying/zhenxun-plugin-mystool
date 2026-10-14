RETIRED_KEYS = frozenset({
    "连接测试开关", "连接测试间隔秒", "插件模块名", "兑换请求间隔秒",
    "兑换持续时间秒", "自动接受好友申请", "好友欢迎语", "启用黑名单",
    "黑名单", "启用白名单", "白名单", "启用管理员名单", "管理员名单",
    "日志目录", "日志路径", "日志轮换", "日志输出开关",
    "PREFERENCE", "GOOD_LIST_IMAGE_CONFIG",
})
PLUGIN_MODULES = ("zhenxun_plugin_mystool", "nonebot_plugin_mystool", "mystool")


def remove_retired_configs(manager):
    retired = {key.upper() for key in RETIRED_KEYS}
    removed = []
    for module in PLUGIN_MODULES:
        if module not in manager.keys():
            continue
        configs = manager[module].configs
        for key in list(configs):
            if key.upper() in retired:
                configs.pop(key)
                removed.append(f"{module}:{key}".lower())
    if removed:
        manager.add_module[:] = [key for key in manager.add_module if key not in removed]
    return removed
