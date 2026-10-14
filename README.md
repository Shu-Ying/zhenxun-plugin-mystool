```
 __    __     __  __     ______     ______   ______     ______     __
/\ "-./  \   /\ \_\ \   /\  ___\   /\__  _\ /\  __ \   /\  __ \   /\ \
\ \ \-./\ \  \ \____ \  \ \___  \  \/_/\ \/ \ \ \/\ \  \ \ \/\ \  \ \ \____
 \ \_\ \ \_\  \/\_____\  \/\_____\    \ \_\  \ \_____\  \ \_____\  \ \_____\
  \/_/  \/_/   \/_____/   \/_____/     \/_/   \/_____/   \/_____/   \/_____/
```

[![CodeFactor](https://www.codefactor.io/repository/github/ljzd-pro/nonebot-plugin-mystool/badge?style=for-the-badge)](https://www.codefactor.io/repository/github/ljzd-pro/nonebot-plugin-mystool)
[![最新发行版](https://img.shields.io/github/v/release/Ljzd-PRO/nonebot-plugin-mysTool?logo=python&style=for-the-badge)](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/releases/latest)
[![最后提交](https://img.shields.io/github/last-commit/Ljzd-PRO/nonebot-plugin-mysTool/dev?style=for-the-badge)](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/commits/dev)
![PyPI 下载量](https://img.shields.io/pypi/dm/nonebot-plugin-mystool?style=for-the-badge)

# mysTool - 米游社辅助工具插件

## 真寻适配版

本分支作为 `zhenxun_bot` 外部插件运行，使用真寻的消息模块和
`nonebot-plugin-alconna`，不再依赖 SAA。需要 Python 3.10+、Pydantic 2
及真寻环境；原数据模型使用 `pydantic.v1` 保持 JSON 格式兼容。

### 安装与加载

在真寻项目目录安装本地插件及其依赖（按实际路径替换）：

```powershell
uv pip install --python .venv/Scripts/python.exe D:/Work/zhenxun-plugin-mystool
```

Linux 使用 `.venv/bin/python`。将本仓库的 `zhenxun_loader.py` 复制为
真寻 `zhenxun/plugins/mystool_loader.py`：

```powershell
Copy-Item D:/Work/zhenxun-plugin-mystool/zhenxun_loader.py zhenxun/plugins/mystool_loader.py
```

从真寻项目目录通过 `uv run zx` 启动。入口使用 NoneBot `require` 加载已安装
的插件，真寻会自动扫描自己的 `zhenxun/plugins` 目录。不要同时复制完整插件
源码或通过其他入口重复加载。当前目录扫描要求路径位于机器人工作目录内，
因此不要将外部仓库的绝对 `src` 路径直接配置为 `EXT_PATH`。
OneBot V11 使用真寻已有适配器；如需 QQ 官方频道，还须在真寻配置其
HTTP/WebSocket 驱动、官方 Bot 凭据，并启用 `QQ_ADAPTER_LOAD`。
安装 QQ 适配器依赖本身不会启用官方 Bot。

### 命令与帮助

默认插件前缀为 `米游社`：`米游社登录`、`米游社签到`、`米游社任务`、
`米游社帮助`、`米游社帮助 登录`。若真寻设置了 `/` 等命令起始字符，
在上述命令前加该字符。插件不会注册无前缀的 `签到`、`帮助`、`设置`，
因此不会占用真寻原命令。真寻帮助及插件管理可发现“米游社小助手”。

### 配置迁移

首次加载会注册 `data/config.yaml` 中的 `mystool` 配置组；完整配置及注释
由真寻启动初始化生成。可在真寻 Web 管理界面或该文件中修改：

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

`PREFERENCE` 对应原 `configV2.json` 的 `preference`，
`GOOD_LIST_IMAGE_CONFIG` 对应 `good_list_image_config`，内部字段名不变。
其余字段会自动补齐，字段含义可参考源码中的原配置说明。

配置优先级为真寻已有值、旧 JSON 中的缺失字段迁移值、插件默认值。
已有非空命令前缀保留；空前缀统一改为 `米游社`。旧 JSON 不会因本次
配置迁移被删除或覆盖。迁移完成后以真寻配置为准，继续编辑旧 JSON
不会覆盖已有真寻值。配置错误会使插件加载失败并记录错误，应修正后重启。

前缀、任务时间及其他运行配置修改后重启生效。账号、Cookies、用户绑定、
兑换计划和用户通知设置仍保存于真寻工作目录的
`data/nonebot-plugin-mystool/dataV2.json`，不要删除该文件。
原设备和 salt 环境配置继续使用原 `.env` 机制，不混入运行偏好。
更换真寻工作目录时需自行保留原插件数据目录。

### 验证

在具备真寻及本插件依赖的环境中执行：

```powershell
$env:ZHENXUN_ROOT = 'D:/Work/zhenxun_bot'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest tests/test_zhenxun_integration.py -q
```

检查使用临时工作目录和模拟发送，不连接机器人、不执行真实签到或兑换。
实际送达、QQ 频道主动私信额度和图片审核仍需连接测试 Bot 验证。

> [!Note]
> 目前本项目基本不再新增功能，问题修复可能也会比较迟，主要是由于当前项目代码可维护性较差（\
> 以及个人时间安排等。未来可能考虑彻底重做一次

## ⚡ 功能和特性

- 支持QQ聊天和QQ频道
- 短信验证登录，免抓包获取 Cookie
- 自动完成每日米游币任务
- 自动进行游戏签到
- 可制定米游币商品兑换计划，到点兑换（因加入了人机验证，成功率较低）
- 可支持多个 QQ 账号，每个 QQ 账号可绑定多个米哈游账户
- QQ 推送执行结果通知
- 原神、崩坏：星穹铁道状态便笺通知
- 可为登录、每日米游币任务、游戏签到配置人机验证打码平台
- 可配置用户黑名单/白名单

## 📖 使用说明

### 🛠️ 原版 NoneBot2 部署资料

以下链接为原版资料；本分支请使用上面的真寻安装与配置方式。
[🔗Installation](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/wiki/Installation)

### 📖 插件具体使用说明

请查看 -> [🔗Wiki 文档](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/wiki)

### ❓ 获取插件帮助信息

#### 插件命令

```
米游社帮助
```

> [!NOTE]
> 示例使用默认的 `米游社` 插件前缀；自定义前缀时请同步替换。
> 真寻设置了 `/` 等命令起始字符时，还需在命令前加该字符。

## 其他

### 贡献
<a href="https://github.com/Ljzd-PRO/nonebot-plugin-mystool/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=Ljzd-PRO/nonebot-plugin-mystool&max=1000" alt="贡献者"/>
</a>

### 🔨 开发版分支
[**🔨dev**](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/tree/dev)

### 📃 源码说明
[📃Source-Structure](https://github.com/Ljzd-PRO/nonebot-plugin-mystool/wiki/Source-Structure)

### 适配 [绪山真寻Bot](https://github.com/HibiKier/zhenxun_bot) 的分支
- https://github.com/MWTJC/zhenxun-plugin-mystool
- https://github.com/ayakasuki/nonebot-plugin-mystool
