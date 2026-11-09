# Security policy

During initial development, security fixes target the current development branch. After a release, use the latest version; no long-term support branch is promised.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/lavallee/daymark/security/advisories/new) if enabled. If that form is unavailable, open a public issue requesting a private reporting channel without disclosing the vulnerability, credentials, or sensitive records. Do not attach a real database or export.

Include the affected version, a minimal synthetic reproduction, expected behavior, actual behavior, and likely impact. Response times are best effort.

## Trust boundaries

Embedded applications own access control and database-file protection. A supplied SQLite connection participates in application transactions; it does not establish an authorization boundary on its own. Standalone service operators own token provisioning, transport security, filesystem permissions, network exposure, backups, and delivery-provider credentials.

Treat metadata, imported records, and notification payloads as untrusted input. Give delivery sinks only the access they need. Stable delivery keys support deduplication; external side effects cannot be guaranteed exactly once.
