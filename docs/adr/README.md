# Architecture Decision Records
Short records of the decisions that shape Warden — context, decision, consequences, evidence. Each one maps to a failure in `../FAILURE-CATALOG.md` or a gate in `../../plan.md`.

| # | Decision |
|---|---|
| [0001](0001-three-services-three-identities.md) | Three services, three identities |
| [0002](0002-deterministic-first-llm-behind-schema.md) | Deterministic first; the LLM never holds the button |
| [0003](0003-firestore-source-of-truth-polling.md) | Firestore is the source of truth; loops poll |
| [0004](0004-stop-never-delete.md) | STOP, never DELETE |
| [0005](0005-harness-contract.md) | A signed harness contract instead of log scraping |
| [0006](0006-graduated-autonomy.md) | Graduated autonomy per action, with a circuit breaker |
| [0007](0007-verification-means-opening.md) | Verification means opening the artifact |
| [0008](0008-external-watchdog.md) | An external watchdog with its own identity |
| [0009](0009-dashboard-jinja2-design-system.md) | Dashboard: server-rendered templates over one design-system stylesheet |
| [0010](0010-no-login-competition-scope.md) | No login in front of the dashboard (competition scope) |
| [0011](0011-utc-storage-browser-timezone.md) | Store UTC, render in the viewer's time zone |
| [0012](0012-one-minute-tick.md) | One-minute tick and measured SLOs |
