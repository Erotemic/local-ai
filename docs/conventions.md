# Service conventions

A service is a high-level local capability such as ComfyUI, ACE-Step, or
TripoSplat. A service may support many models and workflows.

A service directory normally contains:

```text
README.md
service.toml
compose.yaml
Dockerfile
.env.template
setup.sh
start.sh
scripts/
```

## Lifecycle contract

`./setup.sh` is the sole normal first-run/provisioning entry point. It owns
configuration initialization, directory creation, image building, required
model downloads, and model verification. It prints the resolved paths before
persistent writes or downloads.

`./start.sh` only validates already-provisioned state and starts the service with
`docker compose up --no-build`. It does not create `.env`, build an image, or
download data.

Low-level downloader/checker scripts are maintenance conveniences. They require
configuration to have already been initialized by setup.

## Configuration

Machine-wide policy belongs in the root `.env`. Service `.env` files contain
only service-specific choices and explicit overrides. Both are untracked and
mode `0600` by default.

The checked-in `service.toml` is the source of truth for:

- derived service paths;
- directories setup creates;
- retention semantics;
- required/optional model bundles;
- model sources and destinations;
- expected checkpoint layout;
- primary Compose service and UI metadata.

## Network

Services inherit `LOCAL_AI_BIND_ADDRESS=127.0.0.1` from root configuration.
Set it explicitly to another address only when LAN exposure is wanted.

## GPU selection

GPU policy is service-specific. Expose it through the service `.env` rather than
hard-coding a host device in the Dockerfile.

## Upstream source

An exploratory service may initially follow an upstream branch. Once a setup is
relied on, set its `*_REF` variable to a release tag or commit so rebuilding the
same Git revision recreates the same upstream source.
