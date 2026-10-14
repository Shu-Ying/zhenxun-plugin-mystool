# 配置项审查（2026-10-14）

范围：原有 42 个真寻注册配置，核对配置映射、字段定义和当前源码读取路径。首次清理无效配置 5 项；按用户确认再移除自建名单 6 项和好友处理 2 项，累计删除 13 项，保留 29 项。未修改 README；后续按用户要求定向清理宿主配置中的已删除菜单项，不迁移业务数据。

## 已删除，供人工二次审查

| 真寻配置名 | 原字段 | 删除依据 |
| --- | --- | --- |
| 连接测试开关 | `enable_connection_test` | 仅有字段定义与注册映射，没有连接测试执行路径读取。 |
| 连接测试间隔秒 | `connection_test_interval` | 仅有字段定义与注册映射，没有定时连接测试读取。 |
| 插件模块名 | `plugin_name` | 仅在已注释的旧 PluginMetadata.config 赋值中引用；当前插件身份由加载器及 configuration/keys.py 的 MODULE 确定。同步删除该失效注释块和专用 driver 获取。 |
| 兑换请求间隔秒 | `exchange_latency` | 没有运行读取；services/exchange_jobs.py::execute_exchange_job 直接调用一次 good_exchange_sync。 |
| 兑换持续时间秒 | `exchange_duration` | 没有运行读取；当前兑换任务没有受该字段控制的持续请求循环。 |
| 启用黑名单 | `enable_blacklist` | 按用户确认移除，交由真寻封禁及插件权限钩子控制。 |
| 黑名单 | `blacklist` | 移除插件自建用户名单。 |
| 启用白名单 | `enable_whitelist` | 按用户确认移除；真寻插件访问控制不等同于原用户登录白名单。 |
| 白名单 | `whitelist` | 取消独立的“名单内才可登录”限制，交由宿主控制。 |
| 启用管理员名单 | `enable_admin_list` | 按用户确认移除，使用真寻权限记录。 |
| 管理员名单 | `admin_list` | 代执行仅允许真寻 SUPERUSER 或全局权限等级大于 0 的管理员；群内权限不能代执行。 |
| 自动接受好友申请 | `add_friend_accept` | 用户追加要求删除整项功能；真寻 record_request 已提供 invite_manager.AUTO_ADD_FRIEND（默认 false）。 |
| 好友欢迎语 | `add_friend_welcome` | 随插件好友申请处理整体删除，避免重复处理宿主请求。 |

字段定义、RegisterConfig 映射、自建名单函数和好友请求匹配器同步移除。没有新增兑换重试或改变兑换策略。显式的便笺间隔值仍覆盖新默认值。原神和星穹铁道的账号级便笺开关保留。

后台菜单残留修复：插件加载前清除宿主配置注册表中已经撤销的 mystool 项。按用户授权，已备份并定向清理本机 data/config.yaml、data/configs/plugins2config.yaml 中各两个旧分组；其他插件和值保持不变。运行中进程需要重启加载新插件，旧插件版本仍会重新注册旧项。

## 保留清单

以下为首次真寻注册默认值；已有显式值优先。

| 真寻配置名 | 内部字段 | 默认值 | 实际用途 |
| --- | --- | --- | --- |
| GitHub代理 | github_proxy | https://mirror.ghproxy.com/ | 自动下载字体的 GitHub 地址前缀。 |
| 请求超时秒 | timeout | 10 秒 | 米游社接口、图片等请求超时。 |
| 请求重试次数 | max_retry_times | 3 | 启用重试时最多重试 3 次，共 4 次尝试。 |
| 请求重试间隔秒 | retry_interval | 2 秒 | 请求重试等待间隔。 |
| 兑换时区 | timezone | Asia/Shanghai | 兑换时间转换及展示。 |
| 兑换线程数 | exchange_thread_count | 2 | 每个兑换计划的任务数量和结果汇总。 |
| 日志前缀 | log_head | 空字符串 | 部分日志正文前缀，日志仍由宿主管理。 |
| 文件编码 | encoding | utf-8 | 当前实际传给 ImageFont.truetype 的字体编码参数。 |
| 最大用户数 | max_user | 0 | 登录用户数量限制；0 和 -1 表示不限制。 |
| 命令前缀 | command_start | 米游社 | 命令及帮助前缀；修改后重启。 |
| 任务冷却秒 | sleep_time | 2 秒 | 签到和米游币任务执行间隔。 |
| 每日任务时间 | plan_time | 00:30 | 每日自动签到和任务执行时间，HH:MM。 |
| 便笺检查间隔分钟 | resin_interval | 720 分钟 | 自动便笺检查；也用于计算部分提醒时间窗口。 |
| 使用全局验证码接口 | global_geetest | false | 是否使用插件全局验证码接口参数。 |
| 验证码接口地址 | geetest_url | null | 全局验证码接口地址。 |
| 验证码接口参数 | geetest_params | null | 验证码接口的额外请求参数。 |
| 验证码接口请求体 | geetest_json | {"gt":"{gt}","challenge":"{challenge}"} | 验证码 JSON 请求体，占位符替换实际值。 |
| 扫码登录应用ID | game_token_app_id | "2"（字符串） | 米游社二维码登录应用标识。 |
| 扫码查询间隔秒 | qrcode_query_interval | 1 秒 | 扫码状态轮询间隔。 |
| 扫码等待时间秒 | qrcode_wait_time | 120 秒 | 扫码登录最长等待时间。 |
| 商品预览图尺寸 | ICON_SIZE | [600, 600] | 单张商品图宽高，像素。 |
| 商品图片宽度 | WIDTH | 2000 | 最终图片宽度，像素。 |
| 商品图间距 | PADDING_ICON | 0 | 商品图纵向间距，像素。 |
| 商品文字顶部间距 | PADDING_TEXT_AND_ICON_Y | 125 | 文字纵向偏移，像素。 |
| 商品文字横向间距 | PADDING_TEXT_AND_ICON_X | 10 | 图文横向间距，像素。 |
| 商品图片字体 | FONT_PATH | null | 自定义字体路径；默认使用缓存或自动下载。 |
| 商品图片字号 | FONT_SIZE | 50 | 图片文字字号。 |
| 商品图片缓存目录 | SAVE_PATH | data/zhenxun_plugin_mystool | 生成图片缓存，相对运行目录。 |
| 商品图片多进程 | MULTI_PROCESS | Windows false；其他平台 true | 多进程生成商品图片。 |

## 保留但需要人工决定的项目

- 日志前缀：真寻已经提供插件日志标识，但这个字段仍有效，删除会取消管理员自定义前缀，本轮保留。
- 文件编码：字段名及帮助中的“文件读写”与当前字体编码用途不完全一致；有实际读取，本轮不将其认定为无用。
- 好友申请已交给宿主；是否自动同意由 invite_manager.AUTO_ADD_FRIEND 控制，插件不修改该宿主配置。
- 交互命令依赖宿主权限钩子；定时业务任务仍遵循账号开关，未另行把封禁状态转为自动任务开关。
- SaltConfig、DeviceConfig：是米游社接口签名和设备参数，经 PluginEnv 读取；不属于这 42 个真寻注册配置，本轮未删除。

## 验证范围

核对全部注册字段与源码读取；使用隔离宿主的配置/YAML、加载、数据库和消息测试，以及语法、打包检查。真实 Bot、米游社生产账号、生产配置文件和其他数据库不在本轮验证范围。
