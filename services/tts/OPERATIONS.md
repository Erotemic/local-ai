# TTS operations

The operational choices are deliberately explicit:

```text
qwentts     quantized Qwen3-TTS on NVIDIA CUDA
kokoro-gpu  Kokoro-FastAPI on the selected NVIDIA GPU
kokoro-cpu  Kokoro-FastAPI without an NVIDIA dependency
```

Normal `./start.sh` operation runs one backend at a time and stops the others.

## Normal server mode: qwentts

For the GTX 1080 Ti server, keep:

```dotenv
TTS_ACTIVE_BACKEND=qwentts
TTS_BIND_ADDRESS=0.0.0.0
TTS_QWENTTS_GPU=1
TTS_QWENTTS_CLAMP_FP16=0
TTS_QWENTTS_NO_FA=0
```

Then:

```bash
./start.sh
./status.sh
```

The raw qwentts engine is on port `11436`; normal clients should use the MP3 +
retry gateway on `11437`.

For the Android reader with the default 0.6B model:

```text
server: http://<server-lan-ip>:11437
model:  qwen-0.6-customvoice-q8-ggml
voice:  ryan
format: mp3
```

## Switch to Kokoro GPU

The pinned GPU image is:

```text
ghcr.io/remsky/kokoro-fastapi-gpu:v0.3.0-amd64
```

Persist:

```dotenv
TTS_ACTIVE_BACKEND=kokoro-gpu
```

or use a one-shot selection:

```bash
TTS_ACTIVE_BACKEND=kokoro-gpu ./start.sh
```

Default client port: `8880`.

## Switch to Kokoro CPU

The pinned CPU image is:

```text
ghcr.io/remsky/kokoro-fastapi-cpu:v0.3.0-amd64
```

Persist:

```dotenv
TTS_ACTIVE_BACKEND=kokoro-cpu
```

or:

```bash
TTS_ACTIVE_BACKEND=kokoro-cpu ./start.sh
```

Default client port: `8881`.

## Direct comparison starts

These do not stop the other runtimes and are intended for controlled tests:

```bash
./start-qwentts.sh
./start-kokoro-gpu.sh
./start-kokoro-cpu.sh
```

Because the two Kokoro variants use ports 8880 and 8881 they can be measured
side-by-side. qwentts raw benchmarking uses port 11436.

## Benchmarks

```bash
./scripts/benchmark.sh qwentts 5
./scripts/benchmark.sh kokoro-gpu 5
./scripts/benchmark.sh kokoro-cpu 5
```

## Security

All three servers are unauthenticated HTTP services. `127.0.0.1` is the safe
default. Bind to `0.0.0.0` only on a trusted LAN/VPN and restrict the exposed
ports with the host firewall when other interfaces are untrusted.

## Candidate experiment backends

Candidate experiments are intentionally not started or provisioned by default.
See `EXPERIMENTS.md` for the full proof protocol.

The additional `TTS_ACTIVE_BACKEND` values are:

```text
indextts25
chatterbox-flash
chatterbox-nano
```

`./start.sh` still enforces exclusive normal operation, so selecting one of
these stops qwentts, both Kokoro variants, and the other candidates first.
Direct `start-*.sh` scripts do not stop unrelated services and are reserved for
controlled multi-GPU experiments.
