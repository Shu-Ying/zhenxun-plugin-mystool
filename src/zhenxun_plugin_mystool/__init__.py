import nonebot
from nonebot import require
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_alconna")
require("nonebot_plugin_apscheduler")

_driver = nonebot.get_driver()
_command_begin = next(iter(_driver.config.command_start), "")

from . import _version
from .model.config import plugin_config, zhenxun_configs
from zhenxun.configs.utils import Command, PluginExtraData

_command_begin += plugin_config.preference.command_start

__plugin_meta__ = PluginMetadata(
    name="米游社小助手插件\n",
    description="米游社工具-每日米游币任务、游戏签到、商品兑换、免抓包登录\n",
    type="application",
    homepage="https://github.com/Shu-Ying/zhenxun-plugin-mystool",
    supported_adapters={"~onebot.v11"},
    usage=
    f"\n🔐 {_command_begin}登录 ➢ 登录绑定米游社账户"
    f"\n📦 {_command_begin}地址 ➢ 设置收货地址ID"
    f"\n🗓️ {_command_begin}签到 ➢ 手动进行游戏签到"
    f"\n📅 {_command_begin}任务 ➢ 手动执行米游币任务"
    f"\n🛒 {_command_begin}兑换 ➢ 米游币商品兑换相关"
    f"\n🎁 {_command_begin}商品 ➢ 查看米游币商品信息(商品ID)"
    f"\n📊 {_command_begin}原神便笺 ➢ 查看原神实时便笺(原神树脂、洞天财瓮等)"
    f"\n📊 {_command_begin}铁道便笺 ➢ 查看星穹铁道实时便笺(开拓力、每日实训等)"
    f"\n⚙️ {_command_begin}设置 ➢ 设置是否开启通知、每日任务等相关选项"
    f"\n🔑 {_command_begin}账号设置 ➢ 设置设备平台、是否开启每日计划任务、频道任务"
    f"\n🔔 {_command_begin}通知设置 ➢ 设置是否开启每日米游币任务、游戏签到的结果通知"
    f"\n🖨️ {_command_begin}导出Cookies ➢ 导出绑定的米游社账号的Cookies数据"
    f"\n🖇️ {_command_begin}用户绑定 ➢ 绑定关联其他聊天平台或其他账号的用户数据"
    f"\n📖 {_command_begin}帮助 ➢ 查看帮助信息"
    f"\n🔍 {_command_begin}帮助 <功能名> ➢ 查看目标功能详细说明"
    "\n\n⚠️你的数据将经过机器人服务器，请确定你信任服务器所有者再使用。",
    extra=PluginExtraData(
        author="Shu-Ying",
        version=_version.__version__,
        menu_type="米游社",
        commands=[Command(command=plugin_config.preference.command_start + name)
                  for name in ("登录", "地址", "签到", "任务", "兑换", "商品",
                               "原神便笺", "铁道便笺", "设置", "账号设置",
                               "通知设置", "导出Cookies", "用户绑定",
                               "帮助")],
        configs=zhenxun_configs,
    ).to_dict()
)

# 防止多进程生成图片时反复调用

from .utils import CommandBegin

_driver.on_startup(CommandBegin.set_command_begin)

# 加载命令

from .command import *

# 加载其他代码

from .api import *
from .model import *
from .utils import *
