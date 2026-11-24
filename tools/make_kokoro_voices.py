#!/usr/bin/env python3
"""
Generate Kokoro samples for ONE voice per invocation.

Run as a separate process per voice so the 82M model is loaded once and
released between voices — running all voices in one process risks the
OS OOM-killing the process on low-RAM machines.

Usage:
    python3 tools/make_kokoro_voices.py af_heart a
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from make_voice_samples import LINES, OUT, wav_duration  # noqa: E402

LABEL_PREFIX = {
    "af_heart": "kokoro_af_heart_us_female",
    "am_michael": "kokoro_am_michael_us_male",
    "bf_emma": "kokoro_bf_emma_gb_female",
    "bm_george": "kokoro_bm_george_gb_male",
}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: make_kokoro_voices.py <voice_id> <lang_code>")
        return 2
    voice, lang = sys.argv[1], sys.argv[2]
    label = LABEL_PREFIX.get(voice, f"kokoro_{voice}")

    import numpy as np
    import soundfile as sf
    from kokoro import KModel, KPipeline

    OUT.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
    known = {m["file"] for m in manifest}

    model = KModel().to("cpu").eval()
    pipe = KPipeline(lang_code=lang, model=model)

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
    print(f"manifest: {len(manifest)} entries", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
