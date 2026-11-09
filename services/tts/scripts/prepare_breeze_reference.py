#!/usr/bin/env python3
"""Inspect or explicitly prepare an authorized Breeze voice reference pair.

No transcription, diarization, or speech alignment is inferred. A selected
window requires the user to supply the exact matching transcript. Originals
are never modified. Prepared files go into an explicit local output folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from longform_benchmark import inspect_wav, sha256, atomic_json, REPO


def safe_name(name: str) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
        raise ValueError('reference name must be 1-64 lowercase letters, digits, underscores or dashes')
    return name


def validate_output_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    # Host-side voice directory already has a repository .gitignore entry.
    ignored = REPO / 'services' / 'tts' / 'voice-references'
    if (path == REPO or REPO in path.parents) and not (path == ignored or ignored in path.parents):
        raise ValueError('prepared references belong outside Git or in the ignored voice-references folder')
    path.mkdir(parents=True, exist_ok=True)
    return path


def describe_pair(wav: Path, transcript: Path) -> dict:
    if not wav.is_file() or not transcript.is_file():
        raise FileNotFoundError('reference WAV and exact matching transcript are both required')
    content = transcript.read_bytes()
    try:
        text = content.decode('utf8').strip()
    except UnicodeDecodeError as exc:
        raise ValueError('reference transcript must be UTF-8') from exc
    if not text:
        raise ValueError('reference transcript is empty')
    media = wav.read_bytes()
    result = inspect_wav(wav)
    result.update(wav_sha256=sha256(media), transcript_sha256=sha256(content),
                  transcript_words=len(text.split()), transcript_characters=len(text))
    return result


def prepare(args: argparse.Namespace) -> dict:
    name = safe_name(args.name)
    output = validate_output_dir(args.output_dir)
    window = args.start is not None or args.end is not None
    if window and (args.start is None or args.end is None
                   or args.start < 0 or args.end <= args.start):
        raise ValueError('a selected window needs 0 <= --start < --end')
    if window and args.selected_transcript is None:
        raise ValueError('a selected window requires --selected-transcript with its exact spoken words')
    text_path = args.selected_transcript or args.transcript
    source = describe_pair(args.wav, args.transcript)
    destination_wav = output / (name + '.wav')
    destination_txt = output / (name + '.txt')
    destination_manifest = output / (name + '.reference.json')
    if not args.force and any(p.exists() for p in [destination_wav, destination_txt, destination_manifest]):
        raise FileExistsError('reference already exists; choose another name or explicitly use --force')
    # Do not allow accidentally replacing the supplied reference or transcript.
    if destination_wav.resolve() == args.wav.resolve() or destination_txt.resolve() == text_path.resolve():
        raise ValueError('source and destination cannot be the same files')
    if window and args.end > source['duration_seconds'] + 0.001:
        raise ValueError('selected window extends beyond reference duration')
    with tempfile.TemporaryDirectory(prefix='breeze-ref-') as tmp:
        temp = Path(tmp)
        output_wav = temp / 'reference.wav'
        if window or args.normalize:
            if not shutil.which('ffmpeg'):
                raise RuntimeError('ffmpeg is required to trim or normalize a reference')
            command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y']
            # Accurate seeking is performed *after* decoding the source.
            command += ['-i', str(args.wav)]
            if window:
                command += ['-ss', str(args.start), '-t', str(args.end - args.start)]
            if args.normalize:
                command += ['-af', 'loudnorm=I=-18:TP=-1.5:LRA=7']
            command += ['-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', str(output_wav)]
            proc = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if proc.returncode:
                raise RuntimeError(f'ffmpeg reference preparation failed: {proc.stderr[-500:]}')
        else:
            shutil.copyfile(args.wav, output_wav)
        prepared = describe_pair(output_wav, text_path)
        transcript_bytes = text_path.read_bytes()
        # Manifest is published after both files as the completion indicator.
        destination_wav.write_bytes(output_wav.read_bytes())
        destination_txt.write_bytes(transcript_bytes)
        record = dict(schema=1, name=name, source_wav_sha256=source['wav_sha256'],
                      source_transcript_sha256=source['transcript_sha256'],
                      prepared=prepared, normalized_loudnorm=args.normalize,
                      selected_window_seconds=([args.start, args.end] if window else None),
                      note='Transcript alignment is supplied by operator, not automatically verified')
        atomic_json(destination_manifest, record)
        return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect', help='show hashes and mechanical WAV diagnostics; do not alter files')
    inspect.add_argument('--wav', required=True, type=Path)
    inspect.add_argument('--transcript', required=True, type=Path)
    prep = sub.add_parser('prepare', help='copy or optionally trim/normalize a private reference pair')
    prep.add_argument('--wav', required=True, type=Path)
    prep.add_argument('--transcript', required=True, type=Path)
    prep.add_argument('--selected-transcript', type=Path, help='required exact transcript when trimming')
    prep.add_argument('--name', required=True)
    prep.add_argument('--output-dir', required=True, type=Path)
    prep.add_argument('--start', type=float)
    prep.add_argument('--end', type=float)
    prep.add_argument('--normalize', action='store_true')
    prep.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    result = describe_pair(args.wav, args.transcript) if args.command == 'inspect' else prepare(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
