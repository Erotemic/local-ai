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
VOICES_DIR = Path(os.environ.get("TTS_VOICES_DIR", "/voices"))
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


def get_available_voices() -> list[str]:
    voices = set()
    if VOICES_DIR.is_dir():
        for p in VOICES_DIR.glob("*.wav"):
            voices.add(p.stem.lower())
    if REFERENCE_WAV.is_file():
        voices.add("ryan")
    return sorted(voices) if voices else ["ryan"]


def resolve_voice_files(voice_name: str) -> tuple[Path | None, str | None]:
    clean_name = voice_name.lower().strip()
    wav_path: Path | None = None
    ref_text: str | None = None

    # Check for <voicename>.wav and <voicename>.txt in voice-references
    if VOICES_DIR.is_dir():
        cand_wav = VOICES_DIR / f"{clean_name}.wav"
        if cand_wav.is_file():
            wav_path = cand_wav
            cand_txt = VOICES_DIR / f"{clean_name}.txt"
            if cand_txt.is_file():
                try:
                    ref_text = cand_txt.read_text(encoding="utf-8").strip()
                except Exception:
                    pass

    # Fallback to default ryan.wav if not in voice-references
    if wav_path is None and clean_name in {"ryan", "default"} and REFERENCE_WAV.is_file():
        wav_path = REFERENCE_WAV
        cand_txt = REFERENCE_WAV.with_suffix(".txt")
        if cand_txt.is_file():
            try:
                ref_text = cand_txt.read_text(encoding="utf-8").strip()
            except Exception:
                pass

    return wav_path, ref_text


def model_discovery_payload() -> dict[str, Any]:
    voices = get_available_voices()
    return {
        "object": "list",
        "data": [{
            "id": MODEL_ALIAS,
            "object": "model",
            "created": int(RUNTIME.loaded_at) if RUNTIME else int(time.time()),
            "owned_by": "local-ai",
            "speakers": voices,
            "default_voice": "ryan" if "ryan" in voices else voices[0],
        }],
    }


def voice_discovery_payload() -> dict[str, Any]:
    voices = get_available_voices()
    default_v = "ryan" if "ryan" in voices else voices[0]
    return {
        "object": "list",
        "default_voice": default_v,
        "voices": voices,
        "data": [{
            "id": v,
            "name": v,
            "model": MODEL_ALIAS,
            "object": "voice",
        } for v in voices],
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
        elif BACKEND == "fish-s2-pro":
            from tools.server.model_manager import ModelManager

            self.fish = ModelManager(
                mode="tts", device=self.device, half=self.dtype == self.torch.float16,
                compile=False, llama_checkpoint_path=str(MODEL_DIR),
                decoder_checkpoint_path=str(MODEL_DIR / "codec.pth"),
                decoder_config_name="modded_dac_vq",
            )
            decoder = self.fish.decoder_model
            self.sample_rate = (decoder.spec_transform.sample_rate if hasattr(decoder, "spec_transform")
                                else decoder.sample_rate)
            self.extra.update({"pipeline": "fish-speech", "reference_conditioned": True})
        elif BACKEND == "breeze-tts2":
            from breeze_infer.runtime import load_runtime, update_generation_config_for_breeze
            # print(f'{help(load_runtime)=}')

            # Breeze requires an explicit device index like "cuda:0" for torch.cuda.set_device
            target_device = "cuda:0" if self.device.startswith("cuda") else self.device

            self.tokenizer, self.model, self.audio_tokenizer = load_runtime(
                Path(MODEL_DIR),
                device=target_device,
                attn_implementation="eager",
            )
            if self.dtype and hasattr(self.model, "to"):
                self.model = self.model.to(self.dtype)

            update_generation_config_for_breeze(self.model)
            self.sample_rate = 24000
            self.breeze_fast = os.environ.get("TTS_BREEZE_FAST", "0") == "1"
            if self.breeze_fast:
                from dataclasses import replace
                from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig
                from models.warmup_profile import load_warmup_profile

                self.breeze_stream = FastBreezeStreamingRuntime(
                    self.model, self.audio_tokenizer,
                    FastStreamingConfig(max_new_tokens=750, max_seq_len=2048,
                                        fast_all=True, repetition_penalty=1.1),
                    tokenizer=self.tokenizer,
                )
                profile = load_warmup_profile(Path("/opt/breeze-tts/configs/fast.json"))
                # Reference audio can produce prompt lengths absent from the
                # upstream profile. Capture those buckets on first use.
                profile = replace(profile, codec_chunk_frames=self.breeze_stream.codec_chunk_frames,
                                  freeze_after_warmup=False)
                manifest = self.breeze_stream.warmup_from_profile(profile)
                print(f"Breeze fast warmup: {manifest['total_elapsed_ms']:.2f} ms", flush=True)
            self.extra.update({
                "pipeline": "breeze_infer",
                "breeze_fast": self.breeze_fast,
                "breeze_fast_graphs_frozen": False if self.breeze_fast else None,
                "attention_implementation": "eager",
                "reference_conditioned": REFERENCE_WAV.exists(),
                "default_speaker": "S0",
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
        if BACKEND == 'breeze-tts2':
            from longform_speech import POLICY, TARGET_WORDS, HARD_MAX_WORDS
            result['long_form'] = {'policy': POLICY, 'target_words': TARGET_WORDS,
                                   'hard_max_words': HARD_MAX_WORDS,
                                   'cfg_scale': 1.0, 'instruction': False}
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
        wav_path, ref_text_from_file = resolve_voice_files(voice)
        if wav_path is None:
            available = get_available_voices()
            raise ValueError(
                f"voice {voice!r} not found. Place {voice}.wav in {VOICES_DIR} (available: {', '.join(available)})"
            )

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
            if BACKEND == "fish-s2-pro":
                from fish_speech.utils.schema import ServeReferenceAudio, ServeTTSRequest

                ref_text = body.get("ref_text") or ref_text_from_file
                if not ref_text:
                    raise ValueError(f"voice {voice!r} needs a transcript in {wav_path.stem}.txt or ref_text")
                request = ServeTTSRequest(
                    text=text, references=[ServeReferenceAudio(audio=wav_path.read_bytes(), text=str(ref_text))],
                    seed=seed, format="wav",
                )
                for result in self.fish.tts_inference_engine.inference(request):
                    if result.code == "error":
                        raise RuntimeError(str(result.error))
                    if result.code == "final" and result.audio is not None:
                        rate, samples = result.audio
                        return wav_bytes_from_float(samples, rate)
                raise RuntimeError("Fish Speech did not return audio")
            if BACKEND == "breeze-tts2":
                from breeze_infer.templates import get_template, prepare_inputs, select_template_name

                request = {
                    "id": f"req-{int(time.time() * 1000)}",
                    "text": text,
                    "speaker": str(body.get("speaker", "S0")),
                }

                # Optional voice direction or voice design instruction
                instruction = body.get("instruction") or body.get("voice_direction") or body.get("direction")
                if instruction:
                    request["instruction"] = str(instruction)

                # Reference audio and transcript from <voice>.wav and <voice>.txt
                if wav_path and wav_path.is_file():
                    ref_text = body.get("ref_text") or ref_text_from_file
                    if not ref_text:
                        raise ValueError(
                            f"voice {voice!r} has reference audio but no transcript. "
                            f"Create {wav_path.stem}.txt or pass 'ref_text' in request."
                        )
                    # Breeze requires 'ref_audio_path' and 'ref_text' together
                    request["ref_audio_path"] = str(wav_path)
                    request["ref_text"] = str(ref_text)

                template_name = select_template_name(request)
                inputs = prepare_inputs(
                    self.tokenizer,
                    self.audio_tokenizer,
                    self.model,
                    [request],
                    get_template(template_name),
                    guidance_scale=float(body.get("cfg_scale", 1.0 if not instruction else 4.0)),
                    guidance_scale_ref=None,
                    guidance_scale_ins=None,
                )

                target_device = getattr(self, "target_device", self.device)
                inputs = {k: v.to(target_device) if hasattr(v, "to") else v for k, v in inputs.items()}

                if self.breeze_fast:
                    from breeze_infer.runtime import set_all_seeds

                    if float(body.get("repetition_penalty", 1.1)) != 1.1:
                        raise ValueError("Breeze fast mode requires repetition_penalty=1.1")
                    if seed is not None:
                        set_all_seeds(seed)
                    chunks = [chunk.audio for chunk in self.breeze_stream.iter_audio_chunks(
                        inputs, request_id=request["id"], seed=seed,
                    )]
                    if not chunks:
                        raise RuntimeError("Breeze fast mode returned no audio")
                    return wav_bytes_from_float(np.concatenate(chunks), self.breeze_stream.sample_rate)

                output_audio = self.model.generate(
                    **inputs,
                    output_audio=True,
                    audio_tokenizer=self.audio_tokenizer,
                    repetition_penalty=float(body.get("repetition_penalty", 1.1)),
                )

                if hasattr(output_audio, "detach"):
                    audio_np = output_audio.detach().cpu().numpy()
                elif hasattr(output_audio, "numpy"):
                    audio_np = output_audio.numpy()
                else:
                    audio_np = np.asarray(output_audio, dtype=np.float32)

                return wav_bytes_from_float(audio_np, self.sample_rate)

        raise AssertionError(BACKEND)


RUNTIME: Runtime | None = None
JOB_QUEUE = None  # Initialized after the inference runtime is loaded.


class Handler(BaseHTTPRequestHandler):
    server_version = "local-ai-candidate-tts/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def send_payload(self, status: int, payload: bytes, content_type: str,
                     extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, status: int, data: Any) -> None:
        self.send_payload(status, json_bytes(data), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        if self.path == "/health":
            self.send_json(HTTPStatus.OK, {"status": "ok", "backend": BACKEND, "model": MODEL_ALIAS})
        elif self.path == "/v1/models":
            self.send_json(HTTPStatus.OK, model_discovery_payload())
        elif self.path in {"/v1/audio/voices", "/v1/voices"}:
            self.send_json(HTTPStatus.OK, voice_discovery_payload())
        elif self.path == "/v1/runtime":
            info = RUNTIME.metadata()
            if BACKEND == 'breeze-tts2':
                info['long_form_jobs'] = {'path': '/v1/audio/jobs',
                                         'cancellation': 'between-passages',
                                         'workers': 1}
            self.send_json(HTTPStatus.OK, info)
        elif self.path.startswith('/v1/audio/jobs/') and JOB_QUEUE is not None:
            from longform_speech import POLICY, timing_header
            suffix = self.path[len('/v1/audio/jobs/'):].split('/')
            if len(suffix) not in (1, 2) or (len(suffix) == 2 and suffix[1] != 'audio'):
                self.send_json(HTTPStatus.NOT_FOUND, {'error': 'not found'})
                return
            job_id = suffix[0]
            if len(suffix) == 1:
                status = JOB_QUEUE.status(job_id)
                self.send_json(HTTPStatus.OK, status) if status else self.send_json(
                    HTTPStatus.NOT_FOUND, {'error': 'unknown or expired speech job'})
            else:
                audio = JOB_QUEUE.audio(job_id)
                if audio is not None:
                    pcm, timing = audio
                    self.send_payload(HTTPStatus.OK, pcm, 'audio/wav',
                        {'X-Local-AI-Speech-Policy': POLICY,
                         'X-Local-AI-Speech-Timings': timing_header(timing)})
                else:
                    state = JOB_QUEUE.status(job_id)
                    self.send_json(HTTPStatus.CONFLICT if state else HTTPStatus.NOT_FOUND,
                                   {'error': 'job audio is not ready'})
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self.path.startswith('/v1/audio/jobs/') or JOB_QUEUE is None:
            self.send_json(HTTPStatus.NOT_FOUND, {'error': 'not found'})
            return
        suffix = self.path[len('/v1/audio/jobs/'):]
        if not suffix or '/' in suffix:
            self.send_json(HTTPStatus.NOT_FOUND, {'error': 'not found'})
            return
        result = JOB_QUEUE.cancel(suffix)
        self.send_json(HTTPStatus.OK, result) if result else self.send_json(
            HTTPStatus.NOT_FOUND, {'error': 'unknown or expired speech job'})

    def do_POST(self) -> None:  # noqa: N802
        assert RUNTIME is not None
        if self.path == '/v1/audio/jobs':
            if JOB_QUEUE is None:
                self.send_json(HTTPStatus.NOT_IMPLEMENTED, {'error': 'speech jobs are not available'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > 32768:
                    raise ValueError('speech job request must be 1–32768 bytes')
                body = json.loads(self.rfile.read(length))
                priority = body.pop('job_priority', 10)
                result = JOB_QUEUE.submit(body, priority=priority)
                self.send_json(HTTPStatus.ACCEPTED, result)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {'error': str(exc)})
            except OverflowError as exc:
                self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {'error': str(exc)})
            return
        if self.path != "/v1/audio/speech":
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            use_long_form = body.get('long_form', False)
            if not isinstance(use_long_form, bool):
                raise ValueError('long_form must be a boolean')
            headers = {}
            if use_long_form:
                if BACKEND != 'breeze-tts2':
                    raise ValueError('long_form is supported only for breeze-tts2')
                if any(body.get(key) for key in ('instruction', 'direction', 'voice_direction')):
                    raise ValueError('paragraph80 long_form does not accept speaking instructions')
                if float(body.get('cfg_scale', 1)) != 1.0:
                    raise ValueError('paragraph80 long_form requires cfg_scale=1.0')
                from longform_speech import POLICY, synthesize_longform, timing_header
                result = synthesize_longform(
                    body.get('input', ''),
                    lambda piece: RUNTIME.synthesize({**body, 'input': piece, 'cfg_scale': 1.0}),
                )
                wav_data = result.audio
                headers = {'X-Local-AI-Speech-Policy': POLICY,
                           'X-Local-AI-Speech-Timings': timing_header(result.timings)}
            else:
                wav_data = RUNTIME.synthesize(body)
            encoded, content_type = convert_wav(wav_data, str(body.get("response_format", "wav")))
            self.send_payload(HTTPStatus.OK, encoded, content_type, headers)
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
    global RUNTIME, JOB_QUEUE
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
    if BACKEND == 'breeze-tts2':
        from speech_jobs import SpeechJobQueue
        JOB_QUEUE = SpeechJobQueue(lambda piece, body: RUNTIME.synthesize(
            {**body, 'input': piece, 'cfg_scale': 1.0}), workers=1)
    print(json.dumps(RUNTIME.metadata(), sort_keys=True), flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"serving {BACKEND} as {MODEL_ALIAS} on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
