# TTS operations

The service exposes three OpenAI-compatible TTS backends. Operationally, run one
at a time with `./start.sh`; the direct `start-*.sh` scripts remain available
for benchmark work.

## Normal server mode: quantized Qwen on a GTX 1080 Ti

Provision the Q8 model and runtime once:

```bash
./setup.sh --accept-defaults --with-model qwen-0.6-customvoice-q8-gguf
```

For a server that must accept Android clients on the trusted LAN, edit
`services/tts/.env` and set:

```dotenv
TTS_ACTIVE_BACKEND=qwentts
TTS_BIND_ADDRESS=0.0.0.0
```

Then start the configured backend exclusively:

```bash
./start.sh
./status.sh
```

`./start.sh` stops the other TTS containers before starting qwentts. The normal
Q8 endpoint is port `11436`; `./status.sh` prints the local URL and a best-effort
LAN URL and queries `/health` and `/v1/models`.

The Android reader should use the LAN URL, not `127.0.0.1`. The model advertised
by qwentts is normally:

```text
qwen-0.6-customvoice-q8-ggml
```

The endpoint is unauthenticated HTTP. Bind to `0.0.0.0` only on a trusted LAN,
and restrict the port with the host firewall if the machine has untrusted
network interfaces.

## Switching backends

Persist a choice in `.env`:

```dotenv
TTS_ACTIVE_BACKEND=wavhost
```

or:

```dotenv
TTS_ACTIVE_BACKEND=kokoro
```

Then run:

```bash
./start.sh
```

For a one-off switch without editing `.env`:

```bash
TTS_ACTIVE_BACKEND=kokoro ./start.sh
```

Ports remain backend-specific so side-by-side benchmark runs are still possible:

```text
qwentts.cpp Q8         11436
Wavhost                11435
Kokoro-FastAPI          8880
```

A stable public port or remote control plane can be added later if the Android
client needs server-side switching. It is intentionally not required for the
current URL-driven client workflow.

## Direct backend starts

These do not stop the other TTS containers and are primarily useful for testing:

```bash
./start-qwentts.sh
./start-wavhost.sh
./start-kokoro.sh
```

## Kokoro paths in Wavhost

Kokoro's Wavhost registry namespace is `hexgrad`, so the canonical paths are:

```text
{TTS_DATA_ROOT}/wavhost/.wavhost/models/manifests/registry/hexgrad/kokoro/latest
/state/.wavhost/models/checkpoints/hexgrad/kokoro/latest
```

The older `registry/kokoro/kokoro` / `checkpoints/kokoro/kokoro` paths are
incorrect and cause local-ai to mis-detect an otherwise successfully pulled
model.
