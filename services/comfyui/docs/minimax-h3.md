# MiniMax H3 on ComfyUI

This service was originally created to run MiniMax H3 on local RTX 3090 GPUs.
The notes are retained as one model recipe rather than defining the identity of
the whole ComfyUI service.

## Repository layouts

The original MiniMax repository remains useful for Diffusers, SGLang,
vLLM-style runtimes, conversion, and experimentation:

```text
/data/hf-repos/MiniMaxAI/MiniMax-H3/
```

Native ComfyUI H3 workflows use repackaged files from `Comfy-Org/MiniMax-H3`:

```text
/data/hf-repos/Comfy-Org/MiniMax-H3/
```

Do not duplicate those files under ComfyUI private state. The service mounts the
canonical model root read-only and `extra_model_paths.yaml` registers the native
H3 layout.

## Provision the native ComfyUI bundle

Preferred path: select the bundle in `services/comfyui/.env`:

```dotenv
COMFYUI_MODEL_BUNDLES=minimax-h3
```

and run:

```bash
./setup.sh
```

For an already-configured service, the low-level maintenance command is:

```bash
./scripts/download_minimax_h3_comfy.sh
```

Check it with:

```bash
./scripts/check_minimax_h3_models.sh
```

The declared recommended files are:

```text
diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
vae/minimax_h3_video_vae_fp16.safetensors
vae/minimax_h3_audio_vae_fp32.safetensors
```

If you intentionally want every precision/quantization variant rather than the
selected bundle, use Hugging Face tooling directly against the same canonical
repository destination. That bulk mirror is not the default local-ai setup.

## Workflows

In ComfyUI's Template Library, use the MiniMax H3 workflows under Video:

- MiniMax H3 T2V
- MiniMax H3 I2V
- MiniMax H3 R2V

T2V and I2V use the FL2VA diffusion model. R2V uses the Ref2VA diffusion model.
The text encoder and both VAEs are shared. Native H3 support is in ComfyUI core;
a Diffusers-in-ComfyUI custom node is not required.

## GPU topology

On the host where this recipe was developed, one RTX 3090 had a wider active
PCIe link than the other. H3 on 24 GB cards moves substantial data between host
RAM and VRAM, so the wider-link GPU is the better primary device for that host.

`PRIMARY_GPU=auto` uses `scripts/gpu_topology.sh` to choose the GPU with the
widest reported active PCIe link. Set an explicit index in `.env` if the host
layout or workload calls for something else.

The second GPU is generally more useful for an independent job than for trying
to shard a single bandwidth-sensitive H3 graph through this local recipe.

## Optional speed work

The official ComfyUI H3 guide documents Sage Attention as an optional speedup.
Add it only after the base native workflow works because the wheel must match
the PyTorch/CUDA build in the container.
