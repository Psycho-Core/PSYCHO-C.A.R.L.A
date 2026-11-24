#!/usr/bin/env python3
"""
Generate the Kokoro voice samples.

Loads the KModel ONCE and shares it across KPipeline instances — creating a
second full pipeline loads a second copy of the 82M model, which OOMs on
low-RAM machines (observed: exit 137 with ~1.9 GB available).
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from make_voice_samples import LINES, OUT, wav_duration  # noqa: E402

KOKORO_SETS = [
    ("kokoro_af_heart_us_female", "af_heart", "a"),
    ("kokoro_am_michael_us_male", "am_michael", "a"),
    ("kokoro_bf_emma_gb_female", "bf_emma", "b"),
    ("kokoro_bm_george_gb_male", "bm_george", "b"),
]


def main() -> int:
    import numpy as np
    import soundfile as sf
    from kokoro import KModel, KPipeline

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    known = {m["file"] for m in manifest}

    print("loading KModel once ...", flush=True)
    model = KModel().to("cpu").eval()
    print("model loaded", flush=True)

    pipelines: dict[str, object] = {}
    for label, voice, lang in KOKORO_SETS:
        if lang not in pipelines:
            pipelines[lang] = KPipeline(lang_code=lang, model=model)
        pipe = pipelines[lang]
        for line_name, text in LINES:
            out = OUT / f"{label}__{line_name}.wav"
            if out.exists() and out.name in known:
                print(f"  skip {label}/{line_name}", flush=True)
                continue
            try:
                chunks = []
                for _g, _ps, audio in pipe(text, voice=voice, speed=1.0):
                    chunks.append(audio)
                if not chunks:
                    raise RuntimeError("no audio produced")
                sf.write(str(out), np.concatenate(chunks), 24000)
            except Exception as exc:
                print(f"  FAIL {label}/{line_name}: {type(exc).__name__}: {str(exc)[:160]}", flush=True)
                continue
            dur = wav_duration(out)
            manifest.append(
                {
                    "file": out.name,
                    "label": label,
                    "engine": "kokoro",
                    "voice": voice,
                    "line": line_name,
                    "text": text,
                    "duration": round(dur, 2),
                }
            )
            known.add(out.name)
            print(f"  ok   {label}/{line_name}  {dur:.2f}s", flush=True)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nmanifest: {len(manifest)} entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
