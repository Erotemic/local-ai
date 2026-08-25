# local-ai contributor notes

This repository stores reproducible local-service recipes. Keep it lightweight.

- A service under `services/` should remain independently runnable.
- Do not introduce a common runtime/orchestrator just to deduplicate a few lines.
- Do not commit credentials, model weights, generated media, caches, or host data.
- Web services bind to `127.0.0.1` by default. Broader exposure must be explicit.
- Canonical shared model repositories should be mounted read-only when possible.
- Mutable application state belongs to that service, not to another service's tree.
- Cross-service data sharing uses an explicit workspace path.
- Prefer warnings over hard failures for optional diagnostics.
- Preserve working service behavior when reorganizing files.
