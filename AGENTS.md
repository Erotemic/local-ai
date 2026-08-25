# local-ai contributor notes

This repository stores reproducible local-service recipes. Keep it lightweight.

- A service under `services/` remains independently runnable through its own
  `./setup.sh` and `./start.sh`.
- `setup.sh` is the normal provisioning entry point. Do not add a required
  download/configuration step that must be run before it.
- `start.sh` does not create config, download data, or build images.
- Persistent paths and model bundles belong in `service.toml` so setup and
  diagnostics can show them before making changes.
- Root `.env` owns machine-wide storage/network policy; service `.env` files own
  service-specific choices and explicit overrides.
- Do not introduce an inference runtime/orchestrator just to deduplicate service
  code. Shared Python is limited to setup/lifecycle mechanics.
- Do not commit credentials, model weights, generated media, caches, or host data.
- Web services bind to `127.0.0.1` by default. Broader exposure must be explicit.
- Canonical shared model repositories should be mounted read-only when possible.
- Mutable application state belongs to that service, not to another service's tree.
- Cross-service data sharing uses an explicit workspace path.
- Prefer warnings over hard failures for optional diagnostics.
- Preserve working service behavior when reorganizing files.
