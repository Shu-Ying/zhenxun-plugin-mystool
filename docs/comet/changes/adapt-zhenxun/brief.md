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
- Register operational preferences and image-generation settings in zhenxun
  data/config.yaml under mystool, with legacy configV2.json migration fallback.
- Check compatibility with the actual local NoneBot/Pydantic environment.
- Preserve account data, cookies, user bindings, exchange plans and notification
  preferences; retain the existing task scheduling semantics.
- Update installation/migration documentation and add focused integration checks.

Repository paths and README files are implementation references, not independent
requirements documents. No change to zhenxun core is currently proposed.

# Non-goals

- Rewrite MiHoYo APIs, login flows, exchange logic or the entire command system.
- Convert account data to the zhenxun ORM or redesign task scheduling.
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
- Explicit zhenxun configuration takes precedence over legacy configV2.json;
   absent fields migrate from legacy configuration, then use defaults. Existing
   account data, cookies, bindings and exchange plans remain readable.
- Scheduled sign-in, coin tasks and note reminders retain their timing and
   notification switches; focused checks do not contact production accounts.
- Documentation explains installation, command/configuration changes and
   verification limits; dependencies permit the target environment. QQ official
   support and its commands are absent from runtime, metadata and dependency
   declarations. requirements.txt installs qrcode and ntplib in the host venv.

# Constraints and invariants

- Use minimal changes and preserve unrelated source and existing comments.
- Preserve legacy stored qq_guild fields only for data-format compatibility;
  no active behavior reads or updates them. Do not delete existing account data.
- Actual delivery needs a connected test bot and is separate from mocked checks.

# Decisions

- The user's latest instruction replaces the earlier requirement to retain QQ
  official guild support: remove that support completely and retain OneBot V11.
- requirements.txt belongs to zhenxun-plugin-mystool; do not change zhenxun core
  or install/uninstall dependencies in the production environment.
- Q1: User selected A. Use a dedicated configurable command prefix. The default
  is 米游社; retain an existing non-empty custom prefix. Empty prefixes resolve
  to 米游社, including migration from the legacy empty default.
- Q2: User selected A. Operational preferences and image-generation settings
  are managed by zhenxun configuration; configV2.json is a legacy migration
  source. Account data keeps its original format and path.
- Priority is explicit zhenxun value, then legacy value for missing fields,
  then plugin default. Migration does not overwrite existing zhenxun values.
- Settings affecting command registration and scheduled timings take effect
  after restart. Live re-registration/rescheduling is outside this change.
- Device/salt environment overrides and user-specific settings retain their
  existing responsibilities; they are not merged into generic preferences.
- A single change is appropriate: messaging, configuration and registration
  share the plugin bootstrap and must be validated together.
