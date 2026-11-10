# MiniMax H3 Image on ComfyUI

This optional bundle uses ComfyUI's native **MiniMax H3 Image** support (ComfyUI
v0.45.0 or later). The Comfy-Org repository repackages the original
[MiniMaxAI/MiniMax-H3-Image](https://huggingface.co/MiniMaxAI/MiniMax-H3-Image)
weights for native ComfyUI loaders. No custom nodes are required.

## Provisioning

The normal setup command builds ComfyUI and downloads the selected model:

```bash
cd ~/code/local-ai/services/comfyui
./setup.sh --with-model minimax-h3-image
./start.sh
```

For repeatable setups, set this in `services/comfyui/.env` instead:

```dotenv
COMFYUI_MODEL_BUNDLES=minimax-h3-image
```

Or select both video and image bundles:

```dotenv
COMFYUI_MODEL_BUNDLES=minimax-h3,minimax-h3-image
```

The download goes to the shared host model store:

```text
/data/services/hf-repos/Comfy-Org/MiniMax-H3-Image/
├── diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors
├── text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors  # only if needed
└── vae/minimax_h3_image_vae_fp16.safetensors
```

If the Qwen3-VL encoder already exists in
`/data/services/hf-repos/Comfy-Org/MiniMax-H3/text_encoders/`, setup reuses it
without copying or re-downloading it. Both repositories are registered in
`extra_model_paths.yaml` and mounted read-only inside ComfyUI. The video H3
bundle remains independently optional.

The selected files are about **21 GB** for the INT8 diffusion checkpoint,
**15.7 GB** for the encoder if missing, and **1.17 GB** for the VAE. Other
quantizations, including a 12.5 GB W4A8 diffusion checkpoint, are available
from [Comfy-Org/MiniMax-H3-Image](https://huggingface.co/Comfy-Org/MiniMax-H3-Image/tree/main).
They are not downloaded automatically, and the default template must be changed
if you choose a different diffusion file.

For an existing configured installation, maintenance commands are:

```bash
./scripts/check_minimax_h3_image_comfy.sh
./scripts/download_minimax_h3_image_comfy.sh
```

To preview the paths and selected model list without downloading:

```bash
./setup.sh --with-model minimax-h3-image --plan
```

## Upgrading an existing ComfyUI installation

The previous recipe pinned `COMFYUI_REF=v0.31.0`. The updated default is
`v0.45.0`, whose native core includes the MiniMax H3 Image conditioning node.
Running `./setup.sh` migrates the old default revision and image tag on a
configuration-schema upgrade, then builds the updated image. Custom version
pins are preserved; if you intentionally pinned an older revision, edit
`COMFYUI_REF` and `COMFYUI_IMAGE` in `services/comfyui/.env` before setup.

Your existing user workflows, custom nodes, inputs and outputs remain mounted
under `COMFY_DATA_ROOT`. Custom nodes may need compatibility updates when moving
from 0.31 to 0.45.

## Workflows

Open the ComfyUI **Templates** browser and select the **MiniMax H3 Image**
text-to-image or editing template. Official downloadable workflows:

- [Text-to-image](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_t2i.json)
- [Image editing](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/image_minimax_h3_image_edit.json)

Select the downloaded files in their respective model loader nodes if the
workflow defaults do not match. The H3 Image conditioning node should appear
with ComfyUI v0.45.0+.

Use **6 sampling steps, guidance 1**; the checkpoint is CFG-distilled, so a
negative prompt is not needed. With width/height set to 0, text-to-image
defaults to 2048 x 2048. Editing follows the first reference image when both
sizes are 0; a VAE connection on the conditioning node enables the reference
image to be encoded as an image latent as well as text conditioning. More
reference images can be attached and referred to as `<Picture 1>`,
`<Picture 2>`, etc.

On RTX 3090-class 24 GB GPUs, expect host RAM and PCIe offloading to play a
role; higher resolutions and more references increase memory use. Start at
the template's default size, then increase after the base workflow succeeds.

## License

MiniMax H3 Image uses its own
[community license](https://huggingface.co/Comfy-Org/MiniMax-H3-Image).
The model card requires separate authorization for certain commercial/hosted
uses, including affiliated annual revenue above USD 1 million or hosting for
third parties. Review the license before commercial use or making the model available to third parties.
