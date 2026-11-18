# Breeze TTS with two GPUs

Run these commands from the `local-ai` repository root:

```bash
./breeze.sh setup
./breeze.sh start
./breeze.sh status
```

`setup` uses the normal TTS setup workflow: it stages configuration for review,
provisions the pinned Breeze bundle, and builds the Breeze and gateway images.
It does not launch inference. Existing storage paths, model pins, references,
custom ports, and binding choices are retained. No extra manual download step is
required. `start` checks configuration, model files, and images; it never builds
images or downloads model weights.

The default deployment has an independent model replica on physical GPU 0 and
another on GPU 1. It improves throughput for independent requests or lecture
jobs. It does not split one model over the two cards, and it does not subdivide
a single lecture job or paragraph automatically. The lecture reader already
supports two active preparation jobs, so queueing multiple lectures can use
both workers. A client sending one request at a time will use one GPU at a time.
No NVLink bridge is needed for these independent replicas.

## Configuration

Edit `services/tts/.env`; `setup` adds the following settings to older files:

```dotenv
TTS_BREEZE_DUAL_GPU_0=0
TTS_BREEZE_DUAL_GPU_1=1
TTS_BREEZE_DUAL_START_TIMEOUT=1800
TTS_BREEZE_GATEWAY_TIMEOUT=1800
TTS_BREEZE_FAST=0
```

GPU indexes must be distinct nonnegative integers. `nvidia-smi` lists physical
indexes and free memory. Each container sees its selected card as `cuda:0`.
Both replicas share the same image, pinned read-only model directory, voice
reference mount, model alias, dtype, and synthesis settings. This does not
pool VRAM between the cards; each must have enough free memory for its replica.

`TTS_BREEZE_FAST=1` enables the existing optional CUDA-graph mode on **both**
replicas. Warmup may take several minutes, and memory requirements increase.
The first request for a new prompt shape may need additional graph capture.
Benchmark warmed-up generation and review the output before choosing it as a
default. Default eager inference remains available with `TTS_BREEZE_FAST=0`.
Changing inference configuration recreates affected containers and loses their
unfinished jobs.

One-shot overrides work without rewriting `.env`:

```bash
TTS_BREEZE_DUAL_GPU_0=0 TTS_BREEZE_DUAL_GPU_1=1 ./breeze.sh start
```

The service-specific entry points remain supported:

```bash
cd services/tts
TTS_ACTIVE_BACKEND=breeze-tts2-dual ./setup.sh
TTS_ACTIVE_BACKEND=breeze-tts2-dual ./start.sh
```

To make that the default for the service entry point, set
`TTS_ACTIVE_BACKEND=breeze-tts2-dual` in its `.env`. The root `breeze.sh start`
selects dual mode regardless of the saved backend.

## Client connection and job routing

Keep the client's existing Breeze URL. `TTS_BREEZE_PORT` is reused, including
an existing custom port; a fresh configuration defaults to 11441. The model
alias and voice names stay unchanged. Only the gateway publishes a host port;
the two workers are private to the Compose network. Host binding defaults to
`127.0.0.1`; retain or explicitly configure `TTS_BIND_ADDRESS` for LAN access.

For a fresh default configuration:

```text
http://127.0.0.1:11441/v1
```

Synchronous speech requests go to a healthy worker with the fewest outstanding
requests and jobs. Async submissions receive a public 32-character hexadecimal
job ID compatible with the lecture reader. Every status, cancellation, and audio
request follows the original worker assignment. Policy/timing response headers
are preserved, including paragraph80 metadata.

The gateway routing table lives in `{TTS_DATA_ROOT}/breeze-gateway/routes.sqlite3`.
It contains opaque public/worker job IDs, worker indexes, states, and timestamps;
it stores no speech text, voice references, or audio. Routes survive a gateway
restart and are pruned after one day of inactivity. The worker's actual pending
jobs and generated results remain in that worker's process memory with the
existing retention policy. Restarting a worker loses those jobs; the gateway
reports the loss rather than silently submitting the same input again.

The gateway reconciles outstanding jobs so a completed job does not keep a
worker artificially busy when its client stops polling. If one worker becomes
unavailable, new submissions can use the other. Existing jobs stay assigned to
their original worker. `/health` reports `degraded` until both return. Normal
startup requires **both** replicas to be healthy.

## Operation and switching modes

```bash
./breeze.sh logs     # Follow both workers and the gateway
./breeze.sh status   # Container state
./breeze.sh stop     # Stop Breeze in either mode; preserve models and references
./breeze.sh single   # Switch back to the existing single-GPU recipe
```

`start` and `single` follow the existing service launcher's policy of stopping
other TTS backends to release GPU memory. `./breeze.sh start --keep-running`
preserves other backends, provided there is sufficient GPU memory. The single
and dual Breeze deployments share a public port and always replace one another;
switching modes interrupts unfinished server jobs. Reader-cached recordings
remain on disk and are unaffected.

For worker runtime details, use your configured port:

```bash
curl -fsS http://127.0.0.1:11441/v1/runtime | python3 -m json.tool
```

The existing runtime fields remain at the top level; `gateway.workers` includes
each replica's runtime and readiness, and `long_form_jobs.workers` reports the
number of ready workers. GPU names and fast-mode flags are reported per worker.
To check actual utilization, submit two independent jobs and watch `nvidia-smi`
on the deployment host. No speedup is assumed without measuring your workload.

## Offline verification

These tests need no GPUs, model downloads, private reference material, or running
TTS server:

```bash
python3 -m unittest discover -s services/tts/scripts -p 'test_breeze*.py' -v
```

They exercise concurrent HTTP synthesis across both fake workers, async job
routing, cancellation, audio retrieval, gateway restart persistence, degraded
operation, safe mode switching, and setup/start separation.
