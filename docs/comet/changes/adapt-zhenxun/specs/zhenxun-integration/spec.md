# Mystool integration with zhenxun

## Runtime and messaging

Mystool loads as a zhenxun external plugin and uses the framework's plugin
metadata, message construction and sending facilities. SAA is not required.
Interactive replies and scheduled private notifications retain their content.
Images may be constructed from downloaded bytes. Bot/adapter selection and QQ
guild source context remain explicit. Failed or unavailable delivery must not
be reported as successful. The existing return contract is preserved at callers.

## Commands

Commands and their help text use a consistent configurable non-empty prefix.
The default is 米游社, producing commands such as 米游社签到 and 米游社帮助
in addition to the host's configured command-start character. Existing non-empty
custom prefixes are retained; empty prefixes fall back to 米游社. Unprefixed
legacy aliases are not registered. Zhenxun help and permission management can
discover the plugin through its metadata. Prefix changes require a restart.

## Configuration and data

Operational preferences and image-generation settings are registered under
mystool in zhenxun data/config.yaml. Explicit zhenxun values take precedence;
missing fields are initialized from legacy configV2.json, then plugin defaults.
Existing zhenxun values are never overwritten by legacy configuration. Migrated
configuration uses the framework's persistence and help-description mechanism.
Invalid configuration is reported without silently replacing user data.

Existing accounts, cookies, user bindings, user notification preferences and
exchange plans remain readable at their existing data paths. Operational
configuration is resolved before command registration and schedule creation.
Device/salt environment overrides and user-specific configuration keep their
existing roles. Changes to startup-bound settings require a restart.

## Scheduling and compatibility

Daily sign-in and coin tasks, exchange plans and periodic note checks preserve
their existing scheduling behavior. Compatibility changes are limited to what
is needed by the local zhenxun dependency environment. No live account actions
are part of automated validation.

## Verification and installation

Focused checks cover plugin loading, metadata, text/image sending, failure
handling, command routing and configuration/data compatibility. Documentation
describes external-plugin installation and any user-visible migration changes.
Mocked delivery and actual connected-bot delivery are reported separately.
