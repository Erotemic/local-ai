# Service conventions

A service is a high-level local capability such as ComfyUI, ACE-Step, or
TripoSplat. A service may support many models and workflows.

A service directory should normally contain:

```text
README.md
compose.yaml
Dockerfile
.env.template
setup.sh
start.sh
scripts/
```

Not every service needs every file. Avoid adding wrappers that do not provide a
real convenience or safety benefit.

## Network

Compose ports bind to `${LOCAL_AI_BIND_ADDRESS:-127.0.0.1}` by default. Set the
address explicitly if LAN access is wanted.

## GPU selection

GPU policy is service-specific. Expose it through the service `.env` rather than
hard-coding a host device in the Dockerfile.

## Upstream source

An exploratory service may initially follow an upstream branch. Once a setup is
relied on, set its `*_REF` variable to a release tag or commit so rebuilding the
same Git revision recreates the same upstream source.
