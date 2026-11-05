#!/usr/bin/env python3
"""Exercise a running OpenAI-compatible speech endpoint."""
import argparse
import array
import math
import io
import json
import sys
import urllib.error
import urllib.request
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--model", help="override the discovered model")
    parser.add_argument("--voice", help="override the discovered default voice")
    parser.add_argument("--text", default="This is a test of the local text to speech service.")
    parser.add_argument("--output", help="save the generated WAV to this path")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    with urllib.request.urlopen(url + "/v1/models", timeout=10) as response:
        models = json.load(response)["data"]
    model = args.model or models[0]["id"]
    selected = next((item for item in models if item.get("id") == model), {})
    voice = args.voice or selected.get("default_voice") or ("af_heart" if "kokoro" in model.lower() else "ryan")
    payload = json.dumps({"model": model, "voice": voice, "input": args.text, "response_format": "wav"}).encode()
    print(f"Testing {url}: model={model}, voice={voice}", flush=True)
    request = urllib.request.Request(url + "/v1/audio/speech", data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        data = response.read()
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels, width, rate, frames = wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()
        samples = wav.readframes(frames)
    if width != 2 or not rate or not channels or len(samples) != frames * channels * width:
        raise ValueError("response is not a complete PCM16 WAV")
    duration = frames / rate
    values = array.array("h")
    values.frombytes(samples)
    if sys.byteorder != "little":
        values.byteswap()
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / 32768 if values else 0.0
    peak = max((abs(value) for value in values), default=0) / 32768
    result = {"channels": channels, "sample_rate": rate, "duration_s": round(duration, 3), "rms": rms, "peak": peak}
    print(json.dumps(result, indent=2))
    if not (0.15 <= duration <= 180 and 1e-4 <= rms <= 0.8):
        raise ValueError("audio failed sanity checks")
    if args.output:
        with open(args.output, "wb") as output:
            output.write(data)
        print(f"Saved {args.output}")
    print("Speech smoke test passed.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError, wave.Error, urllib.error.URLError) as exc:
        if isinstance(exc, urllib.error.HTTPError):
            print(f"ERROR: HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
