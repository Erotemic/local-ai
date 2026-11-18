# Breeze TTS with 1, 2, or N GPUs

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

The explicit `TTS_BREEZE_GPUS` list selects the physical GPUs, with one
independent model replica per listed card. A fresh configuration selects GPU 0. It improves throughput for independent requests or lecture
jobs. It does not split one model over the selected cards, and it does not subdivide
a single lecture job or paragraph automatically. The lecture reader already
supports two active preparation jobs, so queueing multiple lectures can use
multiple workers. A client sending one request at a time will use one GPU at a time.
No NVLink bridge is needed for these independent replicas.

## Configuration

Edit `services/tts/.env`; `setup` adds the following settings to older files:

```dotenv
# Choose ONE list: 0 for one GPU, 0,1 for two, 0,1,2,3 for four.
TTS_BREEZE_GPUS=0,1
TTS_BREEZE_START_TIMEOUT=1800
TTS_BREEZE_GATEWAY_TIMEOUT=1800
TTS_BREEZE_FAST=0
```

GPU indexes must be distinct nonnegative integers. `nvidia-smi` lists physical
indexes and free memory. Each container sees its selected card as `cuda:0`.
All replicas share the same image, pinned read-only model directory, voice
reference mount, model alias, dtype, and synthesis settings. This does not
pool VRAM between the cards; each must have enough free memory for its replica.

`TTS_BREEZE_FAST=1` enables the existing optional CUDA-graph mode on all
replicas. Warmup may take several minutes, and memory requirements increase.
The first request for a new prompt shape may need additional graph capture.
Benchmark warmed-up generation and review the output before choosing it as a
default. Default eager inference remains available with `TTS_BREEZE_FAST=0`.
Changing inference configuration recreates affected containers and loses their
unfinished jobs.

One-shot overrides work without rewriting `.env`:

```bash
TTS_BREEZE_GPUS=0,1,2,3 ./breeze.sh start
```

The service-specific entry points remain supported:

```bash
cd services/tts
TTS_ACTIVE_BACKEND=breeze-tts2 ./setup.sh
TTS_ACTIVE_BACKEND=breeze-tts2 ./start.sh
```

To make that the default for the service entry point, set
`TTS_ACTIVE_BACKEND=breeze-tts2` in its `.env`. The root `breeze.sh start` selects Breeze and honors the saved GPU list.
`./breeze.sh config` prints the selected IDs and GPU/replica count without
starting anything. Setup migrates older single/dual settings; review the resulting
list before starting. An existing explicit list always takes precedence.

## Client connection and job routing

Keep the client's existing Breeze URL. `TTS_BREEZE_PORT` is reused, including
an existing custom port; a fresh configuration defaults to 11441. The model
alias and voice names stay unchanged. Only the gateway publishes a host port;
all workers are private to the Compose network. Host binding defaults to
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
It contains opaque public/worker job IDs, worker endpoints and indexes, states, and timestamps;
it stores no speech text, voice references, or audio. Routes survive a gateway
restart and are pruned after one day of inactivity. The worker's actual pending
jobs and generated results remain in that worker's process memory with the
existing retention policy. Restarting a worker loses those jobs; the gateway
reports the loss rather than silently submitting the same input again.

The gateway reconciles outstanding jobs so a completed job does not keep a
worker artificially busy when its client stops polling. If one worker becomes
unavailable, new submissions can use the remaining healthy workers. Existing jobs stay assigned to
their original worker. `/health` reports `degraded` until all return. Normal
startup requires all replicas to be healthy.

## Operation and changing GPU selection

```bash
./breeze.sh config   # Selected GPU list, count, and fast mode
./breeze.sh logs     # Follow configured workers and gateway
./breeze.sh status   # Saved selection and actual container state
./breeze.sh stop     # Stop all Breeze containers; preserve stored files
```

Edit `TTS_BREEZE_GPUS` and run the same `./breeze.sh start` command to change
capacity. GPU IDs need not be contiguous. No separate single/dual mode exists.
Start stops other TTS backends to release GPU memory; `--keep-running` preserves
them if there is sufficient memory. Removed workers are stopped. Adding a GPU
keeps existing workers when their configuration is unchanged. Removing a GPU or
changing inference settings loses unfinished jobs on affected workers; wait for
active jobs to finish first. Reader-cached recordings remain on disk.

For worker runtime details, use your configured port:

```bash
curl -fsS http://127.0.0.1:11441/v1/runtime | python3 -m json.tool
```

The existing runtime fields remain at the top level; `gateway.workers` includes
each replica's runtime and readiness, and `long_form_jobs.workers` reports the
number of ready workers. Physical GPU IDs, GPU names, and fast-mode flags are reported per worker.
To check actual utilization, submit multiple independent jobs and watch `nvidia-smi`
on the deployment host. No speedup is assumed without measuring your workload.

## Offline verification

These tests need no GPUs, model downloads, private reference material, or running
TTS server:

```bash
python3 -m unittest discover -s services/tts/scripts -p 'test_breeze*.py' -v
```

They exercise concurrent HTTP synthesis across two and four fake workers, async job
routing, cancellation, audio retrieval, gateway restart persistence, degraded
operation, 1/2/N deployment configuration, and setup/start separation.
