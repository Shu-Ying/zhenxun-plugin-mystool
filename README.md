```
 __    __     __  __     ______     ______   ______     ______     __
/\ "-./  \   /\ \_\ \   /\  ___\   /\__  _\ /\  __ \   /\  __ \   /\ \
\ \ \-./\ \  \ \____ \  \ \___  \  \/_/\ \/ \ \ \/\ \  \ \ \/\ \  \ \ \____
 \ \_\ \ \_\  \/\_____\  \/\_____\    \ \_\  \ \_____\  \ \_____\  \ \_____\
  \/_/  \/_/   \/_____/   \/_____/     \/_/   \/_____/   \/_____/   \/_____/
```

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](./pyproject.toml)
[![License](https://img.shields.io/badge/License-MIT-green)](./LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-Shu--Ying%2Fzhenxun--plugin--mystool-black)](https://github.com/Shu-Ying/zhenxun-plugin-mystool)

真寻机器人的米游社小助手：扫码绑定账号、游戏签到、米游币任务、商品兑换及原神 / 星穹铁道便笺提醒。

本项目基于 [Ljzd-PRO/nonebot-plugin-mystool](https://github.com/Ljzd-PRO/nonebot-plugin-mystool) 适配
[zhenxun_bot](https://github.com/zhenxun-org/zhenxun_bot)<br>

<mark>账号登录信息保存在机器人服务器中，请仅在信任服务器维护者的环境使用。</mark>

## 功能

- 使用米游社 App 扫码登录，绑定一个或多个米游社账号。
- 手动或定时进行游戏签到、每日米游币任务，并推送执行结果。
- 查询商品、设置收货地址、创建米游币兑换计划。
- 查询原神、星穹铁道实时便笺，按设置发送提醒。
- 管理账号与通知开关，支持用户数据绑定及黑名单 / 白名单。
- 接入真寻帮助与权限管理，运行配置统一保存在 `data/config.yaml`。

兑换和签到可能受到米游社验证码、接口限制或活动规则影响，具体结果以实际返回为准。

## 安装

### 1. 方法一：真寻插件商店（推荐）

在小真寻后台的插件商店下载即可

### 2. 方法二：下载此项目源码

下载此项目源码，将/src/zhenxun_plugin_mystool文件夹放在小真寻`plugin`目录下<br>
需要注意安装小真寻缺失依赖

```powershell
uv pip install --python .venv/Scripts/python.exe -r zhenxun/plugins/zhenxun-plugin-mystool/requirements.txt
```

## 使用命令

| 命令 | 用途 |
| --- | --- |
| `米游社登录` | 按提示扫码绑定账号 |
| `米游社签到` | 手动进行游戏签到 |
| `米游社任务` | 手动执行米游币任务 |
| `米游社商品` | 查询米游币商品信息 |
| `米游社兑换` | 管理商品兑换计划 |
| `米游社地址` | 设置收货地址 |
| `米游社原神便笺` / `米游社铁道便笺` | 查询实时便笺 |
| `米游社设置` / `米游社账号设置` / `米游社通知设置` | 查看或调整相关设置 |
| `米游社用户绑定` | 关联其他用户的数据 |
| `米游社导出Cookies` | 导出已绑定账号的 Cookies |
| `米游社帮助` | 查看完整帮助 |
| `米游社帮助 登录` | 查看某个功能的参数与说明 |

## 配置

首次加载会注册配置项，真寻启动初始化后生成 `data/config.yaml` 中的 `mystool` 配置组。
可通过真寻 Web 管理界面或直接编辑文件调整：

```yaml
mystool:
  PREFERENCE:
    command_start: 米游社
    timeout: 10
    plan_time: '00:30'
    resin_interval: 60
  GOOD_LIST_IMAGE_CONFIG:
    WIDTH: 2000
    FONT_SIZE: 50
```

| 配置项 | 含义 |
| --- | --- |
| `PREFERENCE.command_start` | 插件内部命令前缀；空值使用 `米游社` |
| `PREFERENCE.timeout` | 网络请求超时，单位为秒 |
| `PREFERENCE.plan_time` | 每日自动签到与米游币任务时间，格式为 `HH:MM` |
| `PREFERENCE.resin_interval` | 便笺检查间隔，单位为分钟 |
| `GOOD_LIST_IMAGE_CONFIG` | 商品列表图片尺寸、字体、缓存等设置 |

其他字段会自动补齐，含义见 [配置模型](./src/zhenxun_plugin_mystool/model/config.py)。
运行配置修改后重启生效。用户级通知开关、账号任务开关继续通过插件命令设置；
原设备和 salt 环境配置仍使用原 `.env` 机制。

## 常见问题

### 提示缺少 `qrcode` 或 `ntplib`

说明小真寻运行环境没有安装完整插件依赖。重新执行上面的安装命令，确认目标解释器是
真寻的 `.venv`，不要只安装到系统 Python。也可以按源码复制方式使用 `requirements.txt` 补装。

### 插件已安装，但没有加载

确认加载入口位于小真寻 `zhenxun/plugins/`，其中的模块名为 `zhenxun_plugin_mystool`。
使用源码复制方式时，确认目录名正确且没有同时保留旧包或加载入口。

### 配置修改后没有生效

重启小真寻，并确认修改的是当前小真寻工作目录中的 `data/config.yaml`。

### 私信或图片未送达

查看 `mystool.log` 及小真寻日志，并检查 OneBot 连接及客户端的发送限制。
没有可用 Bot 或发送失败时，插件不会把结果报告为成功。

## 贡献
<a href="https://github.com/Shu-Ying/zhenxun-plugin-mystool/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Shu-Ying/zhenxun-plugin-mystool&max=1000" alt="贡献者"/>
</a>

## 📃 源码说明
[📃Source-Structure](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/wiki/Source-Structure)

## 致谢
- [Nonebot2](https://github.com/nonebot/nonebot2) *✨ 跨平台 Python 异步机器人框架 ✨*
- [zhenxun_bot](https://github.com/zhenxun-org/zhenxun_bot) *最爱真寻的一集*
- [Ljzd-PRO/nonebot-plugin-mystool](https://github.com/Ljzd-PRO/nonebot-plugin-mystool)

## 许可证

本项目沿用 [MIT License](./LICENSE)。
