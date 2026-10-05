#!/usr/bin/env python3
"""Small OpenAI-compatible client gateway for qwentts.cpp.

The qwentts.cpp server is intentionally kept as the inference engine. This
front-end adds deployment conveniences needed by LAN clients:

* MP3 output by transcoding qwentts WAV with ffmpeg.
* bounded retries for transient qwentts 5xx/transport failures.
* rejection/retry of implausibly short WAV responses.
* transparent discovery/health proxying.

It deliberately does not install models or control GPUs/process topology.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

UPSTREAM = os.environ.get("QWENTTS_UPSTREAM_URL", "http://qwentts:8080").rstrip("/")
HOST = os.environ.get("QWENTTS_GATEWAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("QWENTTS_GATEWAY_PORT", "11437"))
RETRIES = int(os.environ.get("QWENTTS_GATEWAY_RETRIES", "2"))
TIMEOUT = float(os.environ.get("QWENTTS_GATEWAY_UPSTREAM_TIMEOUT", "240"))
MP3_BITRATE_KBPS = int(os.environ.get("QWENTTS_MP3_BITRATE_KBPS", "64"))
RECOVERY_TIMEOUT = float(os.environ.get("QWENTTS_GATEWAY_RECOVERY_TIMEOUT", "90"))
RECOVERY_POLL_INTERVAL = float(os.environ.get("QWENTTS_GATEWAY_RECOVERY_POLL_INTERVAL", "1"))
MAX_BODY = 4 * 1024 * 1024


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _request_upstream(method: str, path: str, body: bytes | None = None) -> tuple[int, Mapping[str, str], bytes]:
    headers = {"Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(UPSTREAM + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as ex:
        return ex.code, dict(ex.headers.items()), ex.read()


def _wait_for_upstream_health(timeout: float = RECOVERY_TIMEOUT) -> tuple[bool, str]:
    """Wait for qwentts to become healthy after a crash/restart.

    Docker already restarts the qwentts container.  A model reload can take
    tens of seconds, so synthesis retries must wait for that recovery instead
    of consuming every retry while the upstream port is still closed.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    last_reason = "upstream has not been checked"
    while True:
        try:
            status, _headers, body = _request_upstream("GET", "/health")
            if 200 <= status < 300:
                return True, "upstream healthy"
            last_reason = f"health HTTP {status}: {body[:200].decode('utf-8', errors='replace')}"
        except Exception as ex:
            last_reason = f"health transport error: {ex}"

        if time.monotonic() >= deadline:
            return False, last_reason
        time.sleep(max(0.1, RECOVERY_POLL_INTERVAL))


def _wav_duration_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data), "rb") as wav:
        rate = wav.getframerate()
        frames = wav.getnframes()
        if rate <= 0 or frames <= 0:
            return 0.0
        return frames / rate


def _minimum_plausible_duration(text: str) -> float:
    # Deliberately permissive: 80 alphanumeric characters/second is far faster
    # than normal speech. This is only intended to catch empty/near-empty codec
    # output such as a 0.16 second result for a full sentence.
    spoken_chars = sum(1 for char in text if char.isalnum())
    return max(0.20, spoken_chars / 80.0)


def _wav_is_plausible(data: bytes, text: str) -> tuple[bool, str]:
    try:
        duration = _wav_duration_seconds(data)
    except (EOFError, wave.Error) as ex:
        return False, f"invalid WAV: {ex}"
    minimum = _minimum_plausible_duration(text)
    if duration < minimum:
        return False, f"implausibly short WAV: {duration:.3f}s < {minimum:.3f}s"
    return True, f"WAV {duration:.3f}s"


def _wav_to_mp3(data: bytes) -> bytes:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-vn",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        f"{MP3_BITRATE_KBPS}k",
        "-f",
        "mp3",
        "pipe:1",
    ]
    completed = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode != 0 or not completed.stdout:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg MP3 transcode failed: {detail or 'no output'}")
    return completed.stdout


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "local-ai-qwentts-gateway/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[qwentts-gateway] " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json_error(self, status: int, message: str) -> None:
        self._send(status, _json_bytes({"error": {"message": message, "type": "server_error"}}), "application/json")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/health", "/v1/models", "/v1/audio/voices", "/v1/voices"}:
            self._send_json_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        try:
            status, headers, body = _request_upstream("GET", self.path)
        except Exception as ex:
            self._send_json_error(HTTPStatus.BAD_GATEWAY, f"qwentts upstream unavailable: {ex}")
            return
        content_type = headers.get("Content-Type", "application/json").split(";", 1)[0]
        self._send(status, body, content_type)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/audio/speech":
            self._send_json_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            self._send_json_error(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
            return
        if length <= 0 or length > MAX_BODY:
            self._send_json_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "invalid request size")
            return
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except Exception as ex:
            self._send_json_error(HTTPStatus.BAD_REQUEST, f"invalid JSON: {ex}")
            return
        if not isinstance(payload, dict):
            self._send_json_error(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
            return
        text = payload.get("input")
        if not isinstance(text, str) or not text.strip():
            self._send_json_error(HTTPStatus.BAD_REQUEST, "'input' must be a non-empty string")
            return
        requested_format = str(payload.get("response_format", "mp3")).lower()
        if requested_format not in {"mp3", "wav"}:
            self._send_json_error(
                HTTPStatus.BAD_REQUEST,
                "qwentts gateway currently supports response_format 'mp3' or 'wav'",
            )
            return

        upstream_payload = dict(payload)
        upstream_payload["response_format"] = "wav"
        upstream_payload["stream"] = False
        upstream_body = _json_bytes(upstream_payload)
        attempts = max(1, RETRIES + 1)
        last_reason = "no attempt completed"

        for attempt in range(1, attempts + 1):
            try:
                status, headers, wav_data = _request_upstream("POST", "/v1/audio/speech", upstream_body)
            except Exception as ex:
                status = 0
                headers = {}
                wav_data = b""
                last_reason = f"transport error: {ex}"
                self.log_message("speech attempt %d/%d failed: %s", attempt, attempts, last_reason)
            else:
                if 200 <= status < 300:
                    plausible, reason = _wav_is_plausible(wav_data, text)
                    if plausible:
                        try:
                            if requested_format == "mp3":
                                encoded = _wav_to_mp3(wav_data)
                                self.log_message(
                                    "speech attempt %d/%d succeeded: %s -> %d-byte MP3 @ %dk",
                                    attempt,
                                    attempts,
                                    reason,
                                    len(encoded),
                                    MP3_BITRATE_KBPS,
                                )
                                self._send(HTTPStatus.OK, encoded, "audio/mpeg")
                            else:
                                self.log_message("speech attempt %d/%d succeeded: %s", attempt, attempts, reason)
                                self._send(HTTPStatus.OK, wav_data, "audio/wav")
                            return
                        except Exception as ex:
                            self._send_json_error(HTTPStatus.BAD_GATEWAY, str(ex))
                            return
                    last_reason = reason
                    self.log_message("speech attempt %d/%d rejected: %s", attempt, attempts, reason)
                else:
                    content_type = headers.get("Content-Type", "application/octet-stream")
                    last_reason = f"upstream HTTP {status}: {wav_data[:300].decode('utf-8', errors='replace')}"
                    self.log_message("speech attempt %d/%d failed: %s", attempt, attempts, last_reason)
                    if 0 < status < 500:
                        # Client/model/voice errors are deterministic and should be
                        # surfaced immediately rather than retried.
                        self._send(status, wav_data, content_type.split(";", 1)[0])
                        return

            if attempt < attempts:
                self.log_message(
                    "waiting up to %.0fs for qwentts recovery before attempt %d/%d",
                    RECOVERY_TIMEOUT,
                    attempt + 1,
                    attempts,
                )
                recovered, recovery_reason = _wait_for_upstream_health()
                if not recovered:
                    last_reason = f"{last_reason}; recovery timed out: {recovery_reason}"
                    self.log_message("qwentts recovery failed: %s", recovery_reason)
                    break
                self.log_message("qwentts recovered; retrying synthesis")

        self._send_json_error(
            HTTPStatus.BAD_GATEWAY,
            f"qwentts synthesis failed after {attempts} attempt(s): {last_reason}",
        )


def main() -> None:
    if RETRIES < 0:
        raise SystemExit("QWENTTS_GATEWAY_RETRIES must be >= 0")
    if MP3_BITRATE_KBPS < 16:
        raise SystemExit("QWENTTS_MP3_BITRATE_KBPS must be >= 16")
    if RECOVERY_TIMEOUT < 0:
        raise SystemExit("QWENTTS_GATEWAY_RECOVERY_TIMEOUT must be >= 0")
    if RECOVERY_POLL_INTERVAL <= 0:
        raise SystemExit("QWENTTS_GATEWAY_RECOVERY_POLL_INTERVAL must be > 0")
    print(
        f"qwentts gateway listening on {HOST}:{PORT}; upstream={UPSTREAM}; "
        f"retries={RETRIES}; recovery_timeout={RECOVERY_TIMEOUT:.0f}s; "
        f"mp3={MP3_BITRATE_KBPS}k",
        flush=True,
    )
    server = ThreadingHTTPServer((HOST, PORT), GatewayHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
