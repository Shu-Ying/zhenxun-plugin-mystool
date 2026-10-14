# Mystool integration with zhenxun

## Runtime and messaging

Mystool loads as a zhenxun external plugin and uses the framework's plugin
metadata, message construction and sending facilities. SAA and the QQ official
adapter are not required or imported. Only OneBot V11 is supported.
Interactive replies and scheduled private notifications retain their content.
Images may be constructed from downloaded bytes. OneBot bot/adapter selection
remains explicit. QQ official event types, image factories, guild targets,
connection hooks, private-response commands and exception handling are removed.
Failed or unavailable delivery must not
be reported as successful. The existing return contract is preserved at callers.

## Commands

Commands and their help text use a consistent configurable non-empty prefix.
The default is 米游社, producing commands such as 米游社签到 and 米游社帮助
in addition to the host's configured command-start character. Existing non-empty
custom prefixes are retained; empty prefixes fall back to 米游社. Unprefixed
legacy aliases are not registered. Zhenxun help and permission management can
discover the plugin through its metadata. Prefix changes require a restart.

## Configuration and data

Operational preferences and image settings are individual RegisterConfig items
in PluginExtraData.configs. Each entry has a meaningful key, value, help and
default_value, with type validation appropriate to that field. The host's
plugin module stores these in data/config.yaml and exposes them to management.
PREFERENCE and GOOD_LIST_IMAGE_CONFIG grouped dictionaries are removed.
Explicit host values override defaults. No configV2.json or legacy data/config
source is read. Plugin-specific blacklist, whitelist and administrator settings
and helpers are removed. Interactive access is governed by host ban and plugin
permission hooks; no separate plugin user allow-list is retained. Invalid values
report errors before business operations. Automatic friend acceptance defaults
false and periodic note checks default to 720 minutes.
Operational configuration is resolved before command registration and job setup.
Device/salt environment overrides keep their separate role. Startup-bound
settings require restart; the command prefix remains non-empty by default.

Self-service sign-in and coin tasks retain ordinary user access. Commands that
target another user or all users require host SUPERUSER or a positive host
global permission level (LevelUser empty-group scope). A group-specific level
alone must not grant cross-user account access, including when invoked privately.
Authorization is checked before looking up a target or executing any account API.

All mutable business state uses registered zhenxun ORM models in the host's
existing database: user identity/UUID, accounts and cookies, bindings, exchange
plans and per-user switches/settings. Related modifications use the same host
transaction connection; they are awaited before reporting success. Persistence
errors roll back the database and reconcile cached business objects. Concurrent
commands/tasks preserve unrelated state and enforce binding/plan consistency.
No separate database file, Tortoise initialization or production connection
changes are introduced. Multi-record reads use a consistent database snapshot.

Storage initialization runs after host DB schema initialization and before
data-consuming jobs. Persisted accounts, bindings and plans survive restart.
Exchange callbacks use the async transaction path, not synchronous file writes
or unobserved background persistence. External API requests and waits are outside
database transactions. This unreleased version starts from database state only:
remove JSON import/export, upgrade modules, legacy guild compatibility fields,
dataV2.json/plugin_data.json paths and configV2.json paths. Do not import or modify
old files. Image/cache assets and log files remain ordinary files.

## Scheduling and compatibility

Daily sign-in and coin tasks, exchange plans and periodic note checks preserve
their existing scheduling behavior. Compatibility changes are limited to what
is needed by the local zhenxun dependency environment. No live account actions
are part of automated validation.

## Verification and installation

Focused checks cover plugin loading, metadata, text/image sending, failure
handling, command routing, individual configuration registration and real
database behavior. Tests verify startup order, restart restoration, transaction
rollback and independent concurrent updates using isolated temporary databases.
No production database or account actions occur. The user's rewritten README
must remain byte-for-byte unchanged; configuration help and formal change
documents carry new storage/config explanations.
Mocked delivery and actual connected-bot delivery are reported separately.
The plugin repository requirements.txt supplies qrcode>=7.4.2,<9.0.0 and
ntplib>=0.4.0,<0.5.0 for installation into the host's actual virtual environment.
pyproject.toml keeps those direct dependencies for package installation and
removes nonebot-adapter-qq; the regenerated lock follows these declarations.
Checks reject QQ official imports and exercise loading when the adapter cannot
be imported, while retaining OneBot text/image and send-failure coverage.
