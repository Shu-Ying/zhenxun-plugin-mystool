# Outcome

Adapt mystool to the local zhenxun_bot framework. Replace SAA messaging with
zhenxun message construction and sending, and integrate plugin management.

# Scope

- Work in zhenxun-plugin-mystool, using zhenxun_bot as the integration reference.
- Remove SAA imports, registration, image factories and declared dependency.
- Preserve interactive replies and proactive text/image notifications, including
  OneBot V11 and the existing QQ guild direct-message target semantics.
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

- The plugin loads in the target zhenxun environment without SAA, and its
   metadata is available to zhenxun plugin management and help generation.
- Interactive replies and proactive notifications preserve text and image
   content, respect the selected bot/adapter and QQ guild context, and report
   delivery failure without claiming success.
- Commands use the configured non-empty prefix (default 米游社); help examples
   match the registered commands, and unprefixed zhenxun commands remain usable.
- Explicit zhenxun configuration takes precedence over legacy configV2.json;
   absent fields migrate from legacy configuration, then use defaults. Existing
   account data, cookies, bindings and exchange plans remain readable.
- Scheduled sign-in, coin tasks and note reminders retain their timing and
   notification switches; focused checks do not contact production accounts.
- Documentation explains installation, command/configuration changes and
   verification limits; dependencies permit the target environment.

# Constraints and invariants

- Use minimal changes and preserve unrelated source and existing comments.
- QQ guild proactive messaging needs its source guild context; a plain user
  target must not silently replace that contract.
- Actual delivery needs a connected test bot and is separate from mocked checks.

# Decisions

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
