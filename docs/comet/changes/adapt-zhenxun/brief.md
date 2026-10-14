# Outcome

Adapt mystool to the local zhenxun_bot framework. Replace SAA messaging with
zhenxun message construction and sending, and integrate plugin management.

# Scope

- Work in zhenxun-plugin-mystool, using zhenxun_bot as the integration reference.
- Remove SAA imports, registration, image factories and declared dependency.
- Preserve interactive replies and proactive text/image notifications for
  OneBot V11. Remove QQ official adapter imports, runtime branches, hooks,
  guild targets, exceptions and the QQ-specific private-response command.
- Remove nonebot-adapter-qq from declared dependencies and regenerate the lock.
- Add qrcode>=7.4.2,<9.0.0 and ntplib>=0.4.0,<0.5.0 to the plugin repository's
  requirements.txt, consistent with pyproject.toml, with installation guidance.
- Register zhenxun plugin metadata, help commands and management settings.
- Use a configurable non-empty command prefix, defaulting to 米游社.
- Register each adjustable preference and image setting individually with
  RegisterConfig(key, value, help, default_value) inside PluginExtraData.configs.
  Use the plugin's own zhenxun configuration module, meaningful individual keys,
  correct value types and validation; remove the two grouped dictionaries.
- Store all business data in zhenxun's existing database through its Model base
  and transaction facility: users, accounts, cookies, bindings, exchange plans,
  notification switches and reminder settings. No separate database connection.
- Replace synchronous JSON writes at every command, task and exchange callback
  with awaited transactional repository operations. Database commit failure must
  roll back related changes, including cached state; concurrent updates must not
  overwrite unrelated users or leave dangling bindings/plans.
- Initialize storage after zhenxun database startup and before scheduled jobs
  consume data. Restore active plans from database on restart.
- Treat this unreleased version as a fresh schema: remove legacy JSON paths,
  readers/writers, startup upgrade modules and compatibility-only guild fields.
- Remove plugin-specific blacklist, whitelist and administrator entries and
  their helpers. Interactive access uses the host's ban/plugin permission hooks.
  Do not introduce a separate user whitelist; host plugin enable/disable policy
  replaces the previous plugin-specific allow-list behavior.
- Cross-user sign-in/coin-task commands allow only host superusers or host global
  permission administrators (positive level in the global empty-group scope).
  Group-scoped administrators cannot operate other users' accounts.
- Default automatic friend acceptance to false and note checks to 720 minutes;
  explicit host values still override these defaults.
- Check compatibility with the actual local NoneBot/Pydantic environment.
- Retain business behavior, command prefixes, OneBot messaging and scheduling
  semantics. Add isolated real-database checks without live account actions.
- Never edit, rewrite or regenerate the user's README, including during review
  or delivery. Use code/config help and formal change documents for explanations.

Repository paths and README files are implementation references, not independent
requirements documents. No change to zhenxun core is currently proposed.

# Non-goals

- Rewrite MiHoYo APIs, login flows, exchange logic or the entire command system.
- Import, back up, migrate or support old JSON data or old grouped config values.
- Redesign MiHoYo APIs, task scheduling, or the host's database/config framework.
- Publish releases, push changes, or perform real login/sign-in/exchange actions.

# Acceptance examples

- The plugin loads in the target zhenxun environment without SAA or the QQ
   official adapter, and its
   metadata is available to zhenxun plugin management and help generation.
- Interactive replies and proactive notifications preserve text and image
   content, respect the selected OneBot bot/adapter, and report
   delivery failure without claiming success.
- Commands use the configured non-empty prefix (default 米游社); help examples
   match the registered commands, and unprefixed zhenxun commands remain usable.
- Each configurable setting is separately registered through PluginExtraData
   RegisterConfig entries and round-trips through the host config generation and
   reload with its correct type, help and default. No old grouped dictionaries,
   configV2.json or access-list text files are read or written. Plugin-specific
   access lists are absent; friend acceptance defaults false and note interval
   defaults 720 minutes. Host permissions protect interactive commands and
   cross-user operations require superuser/global administrator privileges.
- Users, accounts, cookies, bindings, exchange plans and per-user settings are
   durably stored in the host database. Fresh initialization and restart restore
   data; transaction failures roll back and concurrency preserves independent
   updates. No business JSON files are created or used.
- Scheduled sign-in, coin tasks and note reminders retain their timing and
   notification switches; focused checks do not contact production accounts.
- The user's README remains byte-for-byte unchanged. Code/configuration help and
   formal change documents explain storage/config behavior and verification
   limits; dependencies permit the target environment. QQ official
   support and its commands are absent from runtime, metadata and dependency
   declarations. requirements.txt installs qrcode and ntplib in the host venv.

# Constraints and invariants

- Use minimal changes and preserve unrelated source and existing comments.
- No production database, Bot or account operations during implementation or
  verification; tests use isolated temporary databases and mocked network calls.
- Database transactions do not enclose network requests or waiting for user input.
- Use the host's registered ORM models, connection and lifecycle; no fire-and-
  forget writes that report success before commit.
- Preserve the user's rewritten README without any modifications.
- Actual delivery needs a connected test bot and is separate from mocked checks.

# Decisions

- The user's latest instruction replaces the earlier requirement to retain QQ
  official guild support: remove that support completely and retain OneBot V11.
- requirements.txt belongs to zhenxun-plugin-mystool; do not change zhenxun core
  or install/uninstall dependencies in the production environment.
- The user states this is unreleased and explicitly rejects old-data migration.
- The user selected business data in database and runtime settings managed by
  zhenxun configuration, and supplied individual RegisterConfig entries as the
  required registration format. These supersede the earlier JSON/group migration
  and legacy-field compatibility decisions.
- Q1: User selected A. Use a dedicated configurable command prefix. The default
  is 米游社; retain an existing non-empty custom prefix. Empty prefixes resolve
  to 米游社.
- Operational preferences and image settings use individual zhenxun entries;
  explicit host values take precedence over plugin defaults, without legacy input.
- Settings affecting command registration and scheduled timings take effect
  after restart. Live re-registration/rescheduling is outside this change.
- Device/salt environment overrides and user-specific settings retain their
  existing responsibilities; they are not merged into generic preferences.
- A single change is appropriate: messaging, configuration and registration
  share the plugin bootstrap and must be validated together.
