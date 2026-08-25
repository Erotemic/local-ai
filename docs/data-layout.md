# Data layout

The Git repository describes services. Large and mutable data stays outside it.

## Canonical model repositories

Default:

```text
/data/hf-repos/
```

Use this for Hugging Face repositories or other model trees that are useful to
more than one runtime. A service should normally mount these repositories
read-only and keep application-specific downloads elsewhere.

## Service state

Each service owns its mutable state. Current defaults are:

```text
/data/service/comfyui/
/data/service/docker/ace-step/
/data/service/triposplat/
```

These paths are configurable in the service `.env` files. They are intentionally
outside the Git checkout.

Service state may contain application databases, custom nodes, caches, temporary
files, outputs, and model files downloaded directly by the application. Other
services should not depend on its internal layout.

## Workspaces

Default convention:

```text
/data/local-ai/workspaces/<project>/
```

A workspace contains data intentionally exchanged between services: source
images, generated media selected for another stage, exported `.ply` files, and
similar project artifacts.

Do not make every workspace visible to every container by default. When a
workflow needs sharing, mount the selected workspace explicitly, for example:

```yaml
volumes:
  - ${LOCAL_AI_WORKSPACE}:/workspace:rw
```

This keeps application state private while still allowing multi-service
pipelines when needed.

## Secrets

Keep tokens in untracked `.env` files or another host secret store. Prefer
host-side authenticated model downloads followed by read-only container mounts
when a service does not need ongoing access to the credential.

For `.env` files that contain credentials, mode `0600` is recommended.
