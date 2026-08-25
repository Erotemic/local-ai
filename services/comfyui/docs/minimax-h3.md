# MiniMax H3 on ComfyUI

This service was originally created to run MiniMax H3 on local RTX 3090 GPUs.
The notes are retained here as a model-specific recipe rather than defining the
identity of the whole ComfyUI service.

## Two useful repository layouts

The original MiniMax repository remains useful for Diffusers, SGLang,
vLLM-style runtimes, conversion, and experimentation:

```text
/data/hf-repos/MiniMaxAI/MiniMax-H3/
```

Native ComfyUI H3 workflows use repackaged files from `Comfy-Org/MiniMax-H3`:

```text
/data/hf-repos/Comfy-Org/MiniMax-H3/
```

Do not duplicate those files under ComfyUI private state. The service mounts
`/data/hf-repos` read-only and `extra_model_paths.yaml` registers the native H3
layout.

## Download the native ComfyUI representation

Recommended FL2VA + Ref2VA set:

```bash
./scripts/download_minimax_h3_comfy.sh
```

Mirror every available precision/quantization variant:

```bash
./scripts/download_minimax_h3_comfy.sh --all-variants
```

Check the recommended set:

```bash
./scripts/check_minimax_h3_models.sh
```

The recommended files are:

```text
diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
vae/minimax_h3_video_vae_fp16.safetensors
vae/minimax_h3_audio_vae_fp32.safetensors
```

## Workflows

In ComfyUI's Template Library, use the MiniMax H3 workflows under Video:

- MiniMax H3 T2V
- MiniMax H3 I2V
- MiniMax H3 R2V

T2V and I2V use the FL2VA diffusion model. R2V uses the Ref2VA diffusion model.
The text encoder and both VAEs are shared. Native H3 support is in ComfyUI core;
a Diffusers-in-ComfyUI custom node is not required.

## GPU topology note

On the host where this recipe was developed, one RTX 3090 had a substantially
wider active PCIe link than the other. H3 on 24 GB cards needs significant host
RAM/VRAM movement, so the wider-link GPU is the better primary device.

`PRIMARY_GPU=auto` uses `scripts/gpu_topology.sh` to choose the GPU with the
widest reported active PCIe link. Set an explicit index in `.env` if the host
layout or workload calls for something else.

The second GPU is generally more useful for an independent job than for trying
to shard a single bandwidth-sensitive H3 graph through this local recipe.

## Optional speed work

The official ComfyUI H3 guide documents Sage Attention as an optional speedup.
Add it only after the base native workflow is working because the wheel must
match the PyTorch/CUDA build in the container. The documented workflow also uses
KJNodes to patch Sage Attention into the graph.

## References

- https://docs.comfy.org/tutorials/video/minimax/minimax-h3
- https://github.com/Comfy-Org/ComfyUI/releases
- https://github.com/Comfy-Org/ComfyUI/blob/master/extra_model_paths.yaml.example
