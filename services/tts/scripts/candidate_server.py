#!/usr/bin/env python3
"""Small OpenAI-compatible server for experimental local TTS candidates.

This adapter intentionally exposes one stable API while keeping the model-specific
runtime explicit in /v1/runtime.  It is not a general inference framework.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import subprocess
import tempfile
import threading
import time
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np


BACKEND = os.environ.get("TTS_CANDIDATE_BACKEND", "").strip()
MODEL_ALIAS = os.environ.get("TTS_MODEL_ALIAS", BACKEND).strip()
MODEL_DIR = Path(os.environ.get("TTS_MODEL_DIR", "/models"))
REFERENCE_WAV = Path(os.environ.get("TTS_REFERENCE_WAV", "/voices/ryan.wav"))
LANGUAGE = os.environ.get("TTS_LANGUAGE", "English")
DTYPE_POLICY = os.environ.get("TTS_DTYPE", "auto").lower()
SOURCE_REVISION = os.environ.get("TTS_SOURCE_REVISION", "unknown")
MODEL_REVISION = os.environ.get("TTS_MODEL_REVISION", "unknown")
INDEX_AUX_REVISIONS = {
    "facebook/w2v-bert-2.0": os.environ.get("TTS_INDEXTTS25_W2VBERT_REVISION", "unknown"),
    "amphion/MaskGCT": os.environ.get("TTS_INDEXTTS25_MASKGCT_REVISION", "unknown"),
    "funasr/campplus": os.environ.get("TTS_INDEXTTS25_CAMPPLUS_REVISION", "unknown"),
    "nvidia/bigvgan_v2_22khz_80band_256x": os.environ.get("TTS_INDEXTTS25_BIGVGAN_REVISION", "unknown"),
}


def index_bundle_revision_marker() -> str:
    return ";".join([
        f"main={MODEL_REVISION}",
        f"w2vbert={INDEX_AUX_REVISIONS['facebook/w2v-bert-2.0']}",
        f"maskgct={INDEX_AUX_REVISIONS['amphion/MaskGCT']}",
        f"campplus={INDEX_AUX_REVISIONS['funasr/campplus']}",
        f"bigvgan={INDEX_AUX_REVISIONS['nvidia/bigvgan_v2_22khz_80band_256x']}",
    ])
HOST = os.environ.get("TTS_HOST", "0.0.0.0")
PORT = int(os.environ.get("TTS_PORT", "8080"))


def json_bytes(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True).encode("utf8")


def wav_bytes_from_float(samples: Any, sample_rate: int) -> bytes:
    arr = np.asarray(samples, dtype=np.float32).squeeze()
    if arr.ndim != 1:
        raise ValueError(f"expected mono audio, got shape={arr.shape}")
    arr = np.nan_to_num(arr, nan=0.0, posinf=1.0, neginf=-1.0)
    pcm = np.clip(arr, -1.0, 1.0)
    pcm = (pcm * 32767.0).round().astype("<i2")
    bio = io.BytesIO()
    with wave.open(bio, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(int(sample_rate))
        f.writeframes(pcm.tobytes())
    return bio.getvalue()


def convert_wav(wav_data: bytes, response_format: str) -> tuple[bytes, str]:
    response_format = response_format.lower()
    if response_format in {"wav", "wave"}:
        return wav_data, "audio/wav"
    if response_format == "mp3":
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "wav", "-i", "pipe:0",
                "-f", "mp3", "-b:a", "96k", "pipe:1",
            ],
            input=wav_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError(f"ffmpeg mp3 conversion failed: {proc.stderr.decode(errors='replace')}")
        return proc.stdout, "audio/mpeg"
    raise ValueError(f"unsupported response_format={response_format!r}; use wav or mp3")


def cuda_metadata(torch) -> dict[str, Any]:
    result: dict[str, Any] = {
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "cuda_available": bool(torch.cuda.is_available()),
        "compiled_cuda_arch_list": list(torch.cuda.get_arch_list()) if torch.cuda.is_available() else [],
    }
    if torch.cuda.is_available():
        idx = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(idx)
        result.update({
            "cuda_device_index_inside_container": idx,
            "cuda_device_name": props.name,
            "cuda_compute_capability": list(torch.cuda.get_device_capability(idx)),
            "cuda_total_memory_bytes": int(props.total_memory),
            "cuda_memory_allocated_bytes": int(torch.cuda.memory_allocated(idx)),
            "cuda_memory_reserved_bytes": int(torch.cuda.memory_reserved(idx)),
            "cuda_max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(idx)),
            "cuda_max_memory_reserved_bytes": int(torch.cuda.max_memory_reserved(idx)),
        })
    return result


def resolved_torch_dtype(torch):
    if DTYPE_POLICY in {"float32", "fp32", "f32"}:
        return torch.float32, "float32"
    if DTYPE_POLICY in {"float16", "fp16", "f16"}:
        return torch.float16, "float16"
    if DTYPE_POLICY in {"bfloat16", "bf16"}:
        return torch.bfloat16, "bfloat16"
    if DTYPE_POLICY != "auto":
        raise ValueError(f"unknown TTS_DTYPE={DTYPE_POLICY!r}")
    if not torch.cuda.is_available():
        return torch.float32, "float32"
    major, _minor = torch.cuda.get_device_capability()
    # Pascal does not have native BF16 tensor arithmetic.  Correctness is the
    # priority for this experiment, so keep it in F32 and compare against the
    # same model on Ampere rather than forcing a fragile FP16 path.
    if major < 8:
        return torch.float32, "float32"
    return torch.bfloat16, "bfloat16"


class Runtime:
    def __init__(self) -> None:
        import torch

        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype_policy = DTYPE_POLICY
        if BACKEND == "chatterbox-nano":
            # Nano's pinned upstream loader does not expose a dtype control.
            # Keep its native parameter dtype and report what actually loaded
            # instead of pretending the shared auto policy was applied.
            self.dtype = torch.float32
            self.dtype_name = "upstream-default"
            self.dtype_policy = "upstream-default"
        else:
            self.dtype, self.dtype_name = resolved_torch_dtype(torch)
        self.model: Any = None
        self.sample_rate: int | None = None
        self.loaded_at = time.time()
        self._lock = threading.Lock()
        self.extra: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if BACKEND == "chatterbox-flash":
            from chatterbox_flash import ChatterboxFlashTTS

            self.model = ChatterboxFlashTTS.from_local(
                MODEL_DIR,
                self.device,
                dtype=self.dtype,
                drf_block_size=int(os.environ.get("TTS_FLASH_BLOCK_SIZE", "16")),
            )
            self.model.prepare_conditionals(REFERENCE_WAV)
            self.sample_rate = int(self.model.sr)
            self.extra.update({
                "attention_backend": os.environ.get("TTS_FLASH_ENGINE", "torch"),
                "cuda_graph": os.environ.get("TTS_FLASH_CUDA_GRAPH", "0") == "1",
                "num_steps": int(os.environ.get("TTS_FLASH_STEPS", "10")),
            })
        elif BACKEND == "chatterbox-nano":
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            self.model = ChatterboxTurboTTS.from_local(MODEL_DIR, self.device, nano=True)
            try:
                actual_dtype = next(self.model.t3.parameters()).dtype
                self.dtype_name = str(actual_dtype).removeprefix("torch.")
            except StopIteration:
                pass
            # Use the same reference voice as the other candidates instead of
            # Nano's bundled voice so listening comparisons remain meaningful.
            self.model.prepare_conditionals(REFERENCE_WAV)
            self.sample_rate = int(self.model.sr)
            self.extra.update({"variant": "nano", "reference_conditioned": True})
        elif BACKEND == "indextts25":
            from indextts.infer_v2_5 import IndexTTS2

            use_bf16 = self.dtype is self.torch.bfloat16
            self.model = IndexTTS2(
                cfg_path=str(MODEL_DIR / "config.yaml"),
                model_dir=str(MODEL_DIR),
                device=self.device,
                use_bf16=use_bf16,
                use_cuda_kernel=False,
                use_deepspeed=False,
                use_accel=False,
                use_torch_compile=False,
                use_qwen_emo=False,
            )
            self.sample_rate = 22050
            self.extra.update({
                "auxiliary_model_revisions": INDEX_AUX_REVISIONS,
                "use_bf16": use_bf16,
                "use_cuda_kernel": False,
                "use_accel": False,
                "use_torch_compile": False,
                "use_qwen_emo": False,
            })
        else:
            raise RuntimeError(f"unknown TTS_CANDIDATE_BACKEND={BACKEND!r}")

    def metadata(self) -> dict[str, Any]:
        result = {
            "backend": BACKEND,
            "model_alias": MODEL_ALIAS,
            "model_dir": str(MODEL_DIR),
            "model_revision": MODEL_REVISION,
            "source_revision": SOURCE_REVISION,
            "reference_wav": str(REFERENCE_WAV),
            "device": self.device,
            "dtype_policy": self.dtype_policy,
            "effective_dtype": self.dtype_name,
            "sample_rate": self.sample_rate,
            "loaded_at_unix": self.loaded_at,
            **self.extra,
        }
        result.update(cuda_metadata(self.torch))
        return result

    def _seed(self, seed: int | None) -> None:
        if seed is None:
            return
        self.torch.manual_seed(seed)
        if self.torch.cuda.is_available():
            self.torch.cuda.manual_seed_all(seed)

    def synthesize(self, body: dict[str, Any]) -> bytes:
        text = str(body.get("input", "")).strip()
        if not text:
            raise ValueError("input must be non-empty")
        requested_model = str(body.get("model", MODEL_ALIAS))
        if requested_model not in {MODEL_ALIAS, BACKEND}:
            raise ValueError(f"model {requested_model!r} is not served here; use {MODEL_ALIAS!r}")
        voice = str(body.get("voice", "ryan")).lower()
        if voice not in {"ryan", "default"}:
            raise ValueError("this experiment server exposes only the retained Ryan reference voice")
        seed = body.get("seed")
        if seed is not None:
            seed = int(seed)
        speed = float(body.get("speed", 1.0))
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("speed must be positive")

        with self._lock:
            self._seed(seed)
            if BACKEND == "chatterbox-flash":
                wav = self.model.generate(
                    text,
                    temperature=float(body.get("temperature", 0.6)),
                    num_steps=int(body.get("num_steps", self.extra["num_steps"])),
                    backend=self.extra["attention_backend"],
                    use_cuda_graph=bool(self.extra["cuda_graph"]),
                )
                return wav_bytes_from_float(wav.numpy(), self.sample_rate)
            if BACKEND == "chatterbox-nano":
                wav = self.model.generate(
                    text,
                    temperature=float(body.get("temperature", 0.8)),
                    top_p=float(body.get("top_p", 0.95)),
                    top_k=int(body.get("top_k", 1000)),
                )
                return wav_bytes_from_float(wav.detach().cpu().numpy(), self.sample_rate)
            if BACKEND == "indextts25":
                lang = str(body.get("language", LANGUAGE)).upper()
                duration_factor = 1.0 / speed
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    out_path = Path(tmp.name)
                try:
                    result = self.model.infer(
                        spk_audio_prompt=str(REFERENCE_WAV),
                        text=text,
                        output_path=str(out_path),
                        lang=lang,
                        duration_factor=duration_factor,
                        verbose=False,
                        do_sample=bool(body.get("do_sample", True)),
                        temperature=float(body.get("temperature", 0.8)),
                        top_p=float(body.get("top_p", 0.8)),
                        top_k=int(body.get("top_k", 30)),
                    )
                    if not out_path.is_file() or out_path.stat().st_size < 44:
                        raise RuntimeError(f"IndexTTS did not produce a WAV file (result={result!r})")
                    return out_path.read_bytes()
                finally:
                    out_path.unlink(missing_ok=True)
        raise AssertionError(BACKEND)


RUNTIME: Runtime | None = None


class Handler(BaseHTTPRequestHandler):
    server_version = "local-ai-candidate-tts/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def send_payload(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, data: Any) -> None:
        self.send_payload(status, json_bytes(data), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        if self.path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "backend": BACKEND, "model": MODEL_ALIAS})
        elif self.path == "/v1/models":
            self.send_json(HTTPStatus.OK, {
                "object": "list",
                "data": [{"id": MODEL_ALIAS, "object": "model", "owned_by": "local-ai"}],
            })
        elif self.path == "/v1/runtime":
            self.send_json(HTTPStatus.OK, RUNTIME.metadata())
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        if self.path != "/v1/audio/speech":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            wav_data = RUNTIME.synthesize(body)
            encoded, content_type = convert_wav(wav_data, str(body.get("response_format", "wav")))
            self.send_payload(HTTPStatus.OK, encoded, content_type)
        except (ValueError, json.JSONDecodeError) as ex:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(ex)})
        except Exception as ex:  # keep experiment failures observable to client + logs
            print(f"[error] synthesis failed: {type(ex).__name__}: {ex}", flush=True)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "error": f"{type(ex).__name__}: {ex}",
                "backend": BACKEND,
            })


def provision_indextts25() -> None:
    import shutil

    from huggingface_hub import hf_hub_download, snapshot_download

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="IndexTeam/IndexTTS-2.5",
        revision=MODEL_REVISION,
        local_dir=str(MODEL_DIR),
    )

    cache_dir = MODEL_DIR / "hf_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Reproduce the pinned upstream helper's expected local layout, but pin
    # every auxiliary repository so two machines cannot silently benchmark
    # different bytes simply because HF main advanced between runs.
    w2v_dir = cache_dir / "w2v-bert-2.0"
    snapshot_download(
        repo_id="facebook/w2v-bert-2.0",
        revision=INDEX_AUX_REVISIONS["facebook/w2v-bert-2.0"],
        local_dir=str(w2v_dir),
    )

    def pinned_file(repo_id: str, revision: str, remote_file: str, destination: Path) -> None:
        source = Path(hf_hub_download(
            repo_id=repo_id, filename=remote_file, revision=revision,
        ))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    pinned_file(
        "amphion/MaskGCT", INDEX_AUX_REVISIONS["amphion/MaskGCT"],
        "semantic_codec/model.safetensors", cache_dir / "semantic_codec_model.safetensors",
    )
    pinned_file(
        "funasr/campplus", INDEX_AUX_REVISIONS["funasr/campplus"],
        "campplus_cn_common.bin", cache_dir / "campplus_cn_common.bin",
    )
    bigvgan_dir = cache_dir / "bigvgan"
    for filename in ("config.json", "bigvgan_generator.pt"):
        pinned_file(
            "nvidia/bigvgan_v2_22khz_80band_256x",
            INDEX_AUX_REVISIONS["nvidia/bigvgan_v2_22khz_80band_256x"],
            filename, bigvgan_dir / filename,
        )

    # Let the pinned IndexTTS helper verify that the expected tree is complete;
    # it will find every required auxiliary locally and should not download.
    from indextts.utils.model_download import ensure_models_available
    ensure_models_available(str(MODEL_DIR))

    (MODEL_DIR / ".local-ai-revision").write_text(index_bundle_revision_marker() + "\n")
    (MODEL_DIR / ".local-ai-aux-revisions.json").write_text(
        json.dumps(INDEX_AUX_REVISIONS, indent=2, sort_keys=True) + "\n"
    )
    print(
        f"IndexTTS-2.5 provisioned at {MODEL_DIR} revision={MODEL_REVISION} "
        f"aux={json.dumps(INDEX_AUX_REVISIONS, sort_keys=True)}"
    )


def main() -> None:
    global RUNTIME
    parser = argparse.ArgumentParser()
    parser.add_argument("--provision-only", action="store_true")
    args = parser.parse_args()

    if args.provision_only:
        if BACKEND != "indextts25":
            raise SystemExit("--provision-only is currently only used for indextts25")
        provision_indextts25()
        return

    if not REFERENCE_WAV.is_file():
        raise SystemExit(f"reference WAV is missing: {REFERENCE_WAV}")
    RUNTIME = Runtime()
    print(json.dumps(RUNTIME.metadata(), sort_keys=True), flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"serving {BACKEND} as {MODEL_ALIAS} on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
