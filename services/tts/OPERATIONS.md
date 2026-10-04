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

`./start.sh` stops the other TTS containers before starting qwentts. The raw Q8 engine remains on port `11436`. Normal Android clients should use
the local-ai gateway on port `11437`; `./status.sh` reports that client URL and
queries `/health` and `/v1/models` through the gateway.

The Android reader should use the gateway LAN URL, not `127.0.0.1`. For the
default deployment configure it with:

```text
server: http://<server-lan-ip>:11437
model:  qwen-0.6-customvoice-q8-ggml
voice:  ryan
format: mp3
```

The gateway proxies discovery endpoints from qwentts, converts qwentts WAV to
64 kbps MP3 by default, and retries transient 5xx/empty-or-near-empty generation
results up to `TTS_QWENTTS_GATEWAY_RETRIES` times. The raw engine on `11436` is
still available for debugging and benchmark comparison.

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
qwentts.cpp raw Q8      11436
qwentts MP3 gateway     11437
Wavhost                 11435
Kokoro-FastAPI           8880
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
