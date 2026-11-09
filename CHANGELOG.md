# Changelog

Changes are recorded here using semantic versions. Until 1.0, review release notes carefully before upgrading an embedded application or stored database.

## [0.1.0] - 2026-11-09

### Added

- Typed tasks, projects, goals, people, principals, labels, contexts, filters, and reminders, with explicit lifecycle and structured relationships.
- Separate hard deadlines, planned times, start/target windows, and local-date or timezone-aware temporal values. Bounded fixed-anchor and completion-based recurrence.
- SQLite storage with caller-owned transaction support, expected-version checks, operation replay receipts, event history, and native snapshot import/export. Task deletion cancels and detaches reminders while preserving their original links in history; other inbound references reject atomically. skip advances recurrence without recording completion.
- Optional authenticated HTTP API and Python client, local/remote CLI, and backup/restore commands.
- A recording worker with durable leases, per-claim fencing, retries, and stable delivery keys. The default sink sends no external notifications; custom receivers need persistent deduplication and a qualified transaction design.
- MIT license, synthetic examples, contributor and security guidance, CI, and a release checklist.
