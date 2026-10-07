#!/usr/bin/env python3
"""Mechanical WAV sanity checks for retained TTS experiment outputs."""
from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path

import numpy as np


def inspect(path: Path) -> dict:
    with wave.open(str(path), "rb") as f:
        channels = f.getnchannels()
        width = f.getsampwidth()
        rate = f.getframerate()
        frames = f.getnframes()
        raw = f.readframes(frames)
    if width != 2:
        raise ValueError(f"expected PCM16 WAV, got sample width {width}")
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    duration = frames / rate if rate else 0.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    finite = bool(np.isfinite(samples).all())
    plausible = bool(finite and 0.15 <= duration <= 180.0 and 1e-4 <= rms <= 0.8 and peak <= 1.0)
    return {
        "path": str(path), "channels": channels, "sample_width": width,
        "sample_rate": rate, "frames": frames, "duration_s": duration,
        "peak": peak, "rms": rms, "finite": finite, "plausible": plausible,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    bad = False
    rows = []
    for path in args.paths:
        try:
            row = inspect(path)
        except Exception as ex:
            row = {"path": str(path), "plausible": False, "error": f"{type(ex).__name__}: {ex}"}
        rows.append(row)
        bad |= not row.get("plausible", False)
    print(json.dumps(rows, indent=2, sort_keys=True))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
