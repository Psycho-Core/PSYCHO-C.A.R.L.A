#!/usr/bin/env python3
"""
Generate C.A.R.L.A. voice samples so the persona can be auditioned.

Produces one WAV per (engine, voice, line) into ./voice_samples/ and an
index.html page that lists them with inline <audio> players.

Usage:
    python3 tools/make_voice_samples.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "voice_samples"

PIPER_VOICES = Path.home() / ".local" / "share" / "piper-voices"

# Persona lines actually emitted by core/persona.py.
LINES: list[tuple[str, str]] = [
    ("greeting", "C.A.R.L.A. online and at your service, Boss."),
    ("ack", "On it, Boss."),
    ("done", "Done, Boss. The answer is 42."),
    ("failure", "Apologies, Boss. That didn't go to plan."),
    ("informative", "Right, Boss. The answer is 42."),
    ("dry_wit", "Sir, the diagnostics are clean. That's almost suspicious."),
    ("terse", "Patch applied. No regressions."),
]

# (label, engine, voice_id, engine_arg)
PIPER_SETS = [
    ("piper_alba_gb_female", "piper", "en_GB-alba-medium", None),
    ("piper_cori_gb_female_high", "piper", "en_GB-cori-high", None),
    ("piper_alan_gb_male", "piper", "en_GB-alan-medium", None),
]

KOKORO_SETS = [
    ("kokoro_af_heart_us_female", "kokoro", "af_heart", "a"),
    ("kokoro_am_michael_us_male", "kokoro", "am_michael", "a"),
    ("kokoro_bf_emma_gb_female", "kokoro", "bf_emma", "b"),
    ("kokoro_bm_george_gb_male", "kokoro", "bm_george", "b"),
]


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def run_piper(text: str, voice: str, out: Path) -> tuple[bool, str]:
    model = PIPER_VOICES / f"{voice}.onnx"
    if not model.exists():
        return False, f"model missing: {model}"
    try:
        proc = subprocess.run(
            ["piper", "--model", str(model), "--output_file", str(out)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=300,
        )
        if proc.returncode == 0 and out.exists():
            return True, ""
        return False, (proc.stderr or b"").decode("utf-8", "ignore")[:200]
    except Exception as exc:
        return False, str(exc)[:200]


def run_kokoro(text: str, voice: str, lang: str, out: Path) -> tuple[bool, str]:
    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        pipe = KPipeline(lang_code=lang)
        chunks = []
        for _g, _ps, audio in pipe(text, voice=voice, speed=1.0):
            chunks.append(audio)
        if not chunks:
            return False, "no audio produced"
        data = np.concatenate(chunks)
        sf.write(str(out), data, 24000)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    for label, engine, voice, lang in PIPER_SETS + KOKORO_SETS:
        for line_name, text in LINES:
            out = OUT / f"{label}__{line_name}.wav"
            if engine == "piper":
                ok, err = run_piper(text, voice, out)
            else:
                ok, err = run_kokoro(text, voice, lang, out)
            if not ok:
                print(f"  FAIL {label}/{line_name}: {err}")
                continue
            dur = wav_duration(out)
            manifest.append(
                {
                    "file": out.name,
                    "label": label,
                    "engine": engine,
                    "voice": voice,
                    "line": line_name,
                    "text": text,
                    "duration": round(dur, 2),
                }
            )
            print(f"  ok   {label}/{line_name}  {dur:.2f}s")

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(manifest)} samples -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
