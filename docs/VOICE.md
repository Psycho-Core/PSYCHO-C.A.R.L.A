# Voice — CPU-only dual-channel setup

C.A.R.L.A.'s voice runs entirely on CPU. No GPU, no cloud transcription,
no API keys.

---

## Install

```powershell
pip install -e ".[voice]"
```

That pulls:

| Package | Role |
|---------|------|
| `kokoro` | TTS — 82M params, real-time on CPU |
| `piper-tts` | Alternative TTS — ONNX, very fast |
| `faster-whisper` | STT — CTranslate2, int8 on CPU |
| `sounddevice` | Microphone capture |
| `openwakeword` | Wake word detection — ONNX, offline |
| `onnxruntime` | openWakeWord runtime |
| `soundfile` | WAV I/O |

### Kokoro needs eSpeak NG

Kokoro's phonemiser depends on eSpeak NG, and it uses it as a fallback for
words outside the pronunciation dictionary — which is exactly what
"C.A.R.L.A." is. Without it, those words are silently dropped.

On Windows:

```powershell
winget install --id eSpeak-NG.eSpeak-NG
```

If it isn't on PATH, tell Kokoro where the data lives:

```powershell
$env:ESPEAK_DATA_PATH = "C:\Program Files\eSpeak NG\share\espeak-ng-data"
```

On Linux, install it normally (`apt install espeak-ng`). If you installed it
into a user-local prefix instead of the system library path, the dynamic
loader needs that directory on `LD_LIBRARY_PATH` — and because the loader
reads that only at process start, C.A.R.L.A. restarts itself **once** at
the CLI entrypoint (`carla` / `python -m psycho_carla`) to pick it up. That
never happens inside library code, so it can never interrupt a running
server or test run.

Piper has no such dependency — if Kokoro gives you trouble, Piper is a
one-line config change.

---

## Configuration

```toml
[voice]
enabled          = true

# --- TTS ---
tts_engine       = "kokoro"        # kokoro | piper | silent
tts_voice        = "irish_female"  # irish_female | irish_male | female | male
tts_model        = ""              # explicit voice id or .onnx path
tts_speed        = 1.0
tts_device       = "cpu"

# --- STT ---
stt_engine       = "faster-whisper"  # faster-whisper | whisper | silent
stt_model        = "base.en"
stt_device       = "cpu"
stt_compute_type = "int8"

# --- Wake word ---
wake_word         = "hey carla"
wake_word_enabled = true
wake_word_engine  = "transcript"   # or "openwakeword" + wake_model_path

# --- Behaviour ---
barge_in        = true
channel_split   = true
speak_min_chars = 12
listen_timeout  = 30.0
```

### Voice aliases

`irish_female` and `irish_male` are C.A.R.L.A.'s aliases, resolved per engine:

| Alias | Kokoro | Piper |
|-------|--------|-------|
| `irish_female` | `bf_emma` (British) | `en_GB-alba-medium` |
| `irish_male` | `bm_george` (British) | `en_GB-alan-medium` |
| `female` | `af_heart` (American) | `en_US-amy-medium` |
| `male` | `am_michael` (American) | `en_US-ryan-medium` |

> **There is no Irish voice model.** This was verified against both upstream
> voice manifests: Piper's `voices.json` lists 182 voices and contains **no**
> `en_IE` entry; Kokoro-82M ships American, British, Japanese, Mandarin,
> Spanish, French, Hindi and Italian only.
>
> The `irish_*` aliases therefore resolve to the closest available voices,
> which are **British English** (Dublin-adjacent enough to carry the persona
> well). The alias names are kept stable so that if an Irish voice model
> ever ships, you can change one mapping in `voice/tts.py` and nobody's
> TOML needs editing.

To audition every available voice before choosing, generate the sample set
and open `/voice` in the HUD:

```bash
python3 tools/make_voice_samples.py              # Piper voices
python3 tools/make_kokoro_voices.py bf_emma b    # one Kokoro voice per run
carla voice audition                             # list what exists
```

### Piper voice models

Piper needs its voice files on disk. Put them in:

```
~/.local/share/piper-voices/       # Linux/macOS
%LOCALAPPDATA%\PsychoCarla\piper-voices\   # Windows
```

or set `PIPER_VOICES` to any directory. C.A.R.L.A. searches recursively for
`<voice_id>.onnx`.

Download them from <https://huggingface.co/rhasspy/piper-voices>.

### Kokoro model and memory

Kokoro downloads a ~315 MB checkpoint on first use. Two things worth knowing:

**Point at a local copy.** Set `tts_model_path` to a directory holding
`kokoro-v1_0.pth` to skip the download and keep the model on a fast disk:

```toml
[voice]
tts_engine     = "kokoro"
tts_voice      = "irish_female"
tts_model_path = "C:/Models/kokoro"
```

**Memory.** Loading the checkpoint needs roughly **1.2 GB** of resident memory,
and C.A.R.L.A. loads the model **once** and shares it across voice
instances — constructing a second pipeline would load a second copy and the
OS would kill the process on a low-RAM machine.

If you are running C.A.R.L.A. alongside other services and the process
gets OOM-killed on first speech, switch to Piper:

```toml
tts_engine = "piper"
```

Piper needs ~200 MB and synthesises faster. It is the safer default on a
4 GB or 8 GB machine.

### faster-whisper model

`stt_model` is a faster-whisper model id: `tiny`, `base`, `small`, `medium`,
`large-v3`, or the `.en` variants. Models are downloaded on first use into
`<state dir>/models`.

`base.en` is the sweet spot for CPU: fast and accurate enough for command
phrases. Go bigger only if you have the cores.

---

## The dual channel

Voice is one half of a two-channel output. The other half is the display.

```
Model response
   │
   ├─► SPEECH  → concise summary → TTS → speakers
   │              code, diffs and tables removed
   │
   └─► DISPLAY → full text → HUD
```

Set `channel_split = false` to speak the full response instead — useful when
you want C.A.R.L.A. to read code aloud verbatim.

`speak_min_chars` suppresses vocalising trivially short responses (a bare
"Done." is usually noise).

---

## Wake word

Default (`wake_word_engine = "transcript"`): no trained model is needed.
While someone is speaking, short utterances are transcribed by the speech
recogniser (`faster-whisper`) and checked for the phrase — tolerant of
"hey karla" / "hay carlo" style mis-hearings, and of a command in the same
breath ("hey carla, what's the weather" runs the command directly). Silence
is never transcribed, so the cost is small. Needs the `voice` extra.
Anything that merely *mentions* Carla later in a sentence does not wake her.

Optional (`wake_word_engine = "openwakeword"` + `wake_model_path`): a
dedicated acoustic model is lighter and more reliable in noise, but you must
train a "hey carla" model first (openWakeWord's training notebook). The
bundled openWakeWord models respond to *other* phrases ("hey jarvis"), so
none is used by default.

When the wake word fires:
1. The HUD shows a toast and the Voice Core pulses.
2. C.A.R.L.A. starts listening for the command that follows.
3. The transcript is dropped into the composer and sent.

---

## Barge-in

With `barge_in = true`, a background monitor computes RMS energy on the mic
while C.A.R.L.A. is speaking. Sustained energy above the threshold
interrupts playback mid-sentence.

This is a genuine interruption, not a queue — the speech stops where it is.

---

## Command line

```bash
carla voice status           # what's installed, what's available
carla voice speak "Systems are nominal."
carla voice listen           # record until silence, transcribe, print
```

## Python

```python
from psycho_carla import Carla

f = Carla()
print(f.runtime.voice.status())

# Speak something directly
f.runtime.voice.speak("All systems nominal, Boss.")

# Listen for a command
text = f.runtime.voice.listen_once()
print("Heard:", text)

# Speak only the speech channel of a result
result = f.ask("Write a debounce function in Python.")
f.runtime.voice.speak(result["speech"])   # not result["display"]
```

---

## Microphone notes on Windows

`sounddevice` uses PortAudio, which ships with the wheel — no separate install.

If you have multiple input devices, PortAudio picks the system default.
To choose a specific one:

```python
import sounddevice as sd
print(sd.query_devices())
```

Then pass the device index into `Microphone(device=...)` in
`voice/stt.py`.

Windows also has a microphone privacy toggle:
**Settings → Privacy & security → Microphone** — make sure desktop apps can
access it.

---

## Performance

Everything runs on CPU. Rough expectations on a modern laptop:

| Component | Load | Notes |
|-----------|------|-------|
| Kokoro TTS | ~1.5s per sentence | 82M params, single thread |
| Piper TTS | ~0.3s per sentence | Much faster, ONNX |
| faster-whisper `base.en` | ~0.4x realtime | int8, 2 threads |
| Transcript wake | one short transcription per utterance | CPU, only when speech is heard |

Use Piper if latency matters more than voice quality.

---

## Degradation

If a backend is missing, C.A.R.L.A. does not crash:

| Missing | Behaviour |
|---------|-----------|
| Kokoro | Falls back to Piper, then to `SilentTTS` |
| Piper | Falls back to `SilentTTS` |
| faster-whisper | Falls back to `whisper`, then to `SilentSTT` |
| sounddevice | Microphone reports unavailable; `listen_once` returns `""` |
| openWakeWord | Wake detection disabled; the HUD shows "Wake OFF" |

The UI keeps working in every case. `carla doctor` lists what's present.
