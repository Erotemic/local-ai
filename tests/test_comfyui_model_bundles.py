"""Focused checks for shared ComfyUI weights and default-version migration."""
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest
from unittest.mock import patch

from scripts import local_ai

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = REPO_ROOT / "services" / "comfyui"
MANIFEST = tomllib.loads((SERVICE_DIR / "service.toml").read_text())
IMAGE = next(model for model in MANIFEST["models"] if model["name"] == "minimax-h3-image")


class MiniMaxH3ImageBundleTest(unittest.TestCase):
    def test_shared_video_encoder_satisfies_image_bundle(self):
        with TemporaryDirectory() as tmp:
            env = {"HF_REPOS_ROOT": tmp}
            image_root = local_ai.model_destination(IMAGE, env)
            for rel in (
                "diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors",
                "vae/minimax_h3_image_vae_fp16.safetensors",
            ):
                path = image_root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch()
            self.assertFalse(local_ai.model_complete(IMAGE, env))

            shared_encoder = Path(tmp) / "Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
            shared_encoder.parent.mkdir(parents=True, exist_ok=True)
            shared_encoder.touch()
            self.assertTrue(local_ai.model_complete(IMAGE, env))
            self.assertEqual(local_ai.model_missing(IMAGE, env), [])

    def test_download_omits_encoder_only_if_reusable(self):
        with TemporaryDirectory() as tmp:
            env = {"HF_REPOS_ROOT": tmp}
            with patch.object(local_ai, "run_command") as cmd:
                local_ai.download_huggingface(IMAGE, SERVICE_DIR, env)
            args = cmd.call_args.args[0]
            self.assertIn("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", args)

            shared_encoder = Path(tmp) / "Comfy-Org/MiniMax-H3/text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
            shared_encoder.parent.mkdir(parents=True, exist_ok=True)
            shared_encoder.touch()
            with patch.object(local_ai, "run_command") as cmd:
                local_ai.download_huggingface(IMAGE, SERVICE_DIR, env)
            args = cmd.call_args.args[0]
            self.assertNotIn("text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", args)
            self.assertIn("diffusion_models/minimax_h3_image_pruned_int8_convrot.safetensors", args)
            self.assertIn("vae/minimax_h3_image_vae_fp16.safetensors", args)

    def test_old_defaults_upgrade_without_overwriting_custom_pins(self):
        template = SERVICE_DIR / ".env.template"
        previous_defaults = MANIFEST["config"]["previous_defaults"]
        with TemporaryDirectory() as tmp:
            destination = Path(tmp) / ".env"
            destination.write_text(
                "LOCAL_AI_SERVICE_CONFIG_VERSION=2\n"
                "COMFYUI_IMAGE=local/comfyui:v0.31.0\n"
                "COMFYUI_REF=v0.31.0\n"
                "COMFYUI_PORT=8199\n"
            )
            local_ai.install_env_from_template(
                destination, template, label="test", force_edit=False,
                accept_defaults=True, previous_defaults=previous_defaults,
            )
            values = local_ai.parse_env(destination)
            self.assertEqual(values["LOCAL_AI_SERVICE_CONFIG_VERSION"], "3")
            self.assertEqual(values["COMFYUI_REF"], "v0.45.0")
            self.assertEqual(values["COMFYUI_IMAGE"], "local/comfyui:v0.45.0")
            self.assertEqual(values["COMFYUI_PORT"], "8199")

            destination.write_text(
                "LOCAL_AI_SERVICE_CONFIG_VERSION=2\n"
                "COMFYUI_IMAGE=local/comfyui:custom\n"
                "COMFYUI_REF=v0.32.0\n"
            )
            local_ai.install_env_from_template(
                destination, template, label="test", force_edit=False,
                accept_defaults=True, previous_defaults=previous_defaults,
            )
            values = local_ai.parse_env(destination)
            self.assertEqual(values["COMFYUI_REF"], "v0.32.0")
            self.assertEqual(values["COMFYUI_IMAGE"], "local/comfyui:custom")


if __name__ == "__main__":
    unittest.main()
