# Architecture Deep Dive

PSYCHO-C.A.R.L.A. is organised as five composable primitives. Each can be
replaced, benchmarked or optimised independently. This document explains *why*
the boundaries sit where they do.

---

## The dependency rule

```
core/         ← no dependency on any other layer
providers/    ← depends on core
agents/       ← depends on core, providers, tools
tools/        ← depends on core
memory/       ← depends on core
learning/     ← depends on core
server/       ← depends on everything (it's the composition root)
voice/        ← depends on core
```

`core/` never imports `agents/` or `server/`. The runtime (`core/runtime.py`) is
the one place that knows about all five, and it does so by *composition* — it
receives a router, a tool registry and a memory store, it doesn't build them
from scratch. That is what makes the whole thing testable without a model.

---

## Pillar 1 — Intelligence

`providers/router.py`

The router converts a **capability tier alias** into a concrete
`(adapter, model, sampling)` triple. Aliases are the key abstraction:

```toml
[tiers.fast_tier]
provider = "ollama"
model    = "qwen2.5:7b-instruct"
```

An agent says `tier="fast_tier"`. It never learns that this means Ollama, or
Qwen, or 7B. Swap it:

```toml
[tiers.fast_tier]
provider    = "openai_compat"
model       = "gpt-4o-mini"
base_url    = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
```

Nothing else in the codebase changes. Not the agents, not the HUD, not the API.

**Fallback chain.** `config.intelligence.fallback_tier` names a second tier to
try when the first fails. The router resolves aliases in order and falls back
to the default tier if the requested one doesn't exist.

**Per-tier sampling.** Each tier carries its own `temperature`, `max_tokens`,
`top_p`, `top_k`, `repetition_penalty` and `stop_sequences`, defaulting to the
global `[intelligence]` values. A `deep_tier` can run at `temperature = 0.4`
while a `fast_tier` runs at `0.8` without either knowing about the other.

---

## Pillar 2 — Engine

`providers/base.py` defines the contract every adapter implements:

```python
class ModelAdapter(abc.ABC):
    @abc.abstractmethod
    def generate(
        self,
        messages: Sequence[Message],
        capability_tier: str = "fast_tier",
        stream: bool = False,
        **kwargs: Any,
    ) -> GenerationResult: ...
```

Note `capability_tier` is a *tier*, not a model. Adapters receive the already-
resolved model in `kwargs["model"]`.

### Adding a provider

Three options, in increasing order of effort:

**1. Point `openai_compat` at it.** If it speaks OpenAI, you're done.

**2. Configure `generic_http`.** If it speaks a bespoke JSON dialect:

```toml
[engine.adapters.my_backend]
base_url      = "https://example.local/generate"
prompt_path   = "input"
response_path = "output.text"
static_fields = { stream = false }
```

**3. Write an adapter.** Subclass `ModelAdapter`, implement `generate`, decorate
with `@register_adapter`. ~150 lines for a native protocol.

### The OpenAI-compatible server

`server/api/openai_compat.py` exposes `/v1/chat/completions`, `/v1/models` and
`/v1/completions`. The `model` field accepts a tier alias *or* a raw provider
model id — it resolves to a tier when it can, and falls back to the active tier
otherwise.

Responses include a `carla` extension object with `speech`, `code_blocks`,
`agent`, `tier`, `latency_ms` and `tool_calls`. Standard clients ignore it;
C.A.R.L.A.-aware clients get the dual channel for free.

---

## Pillar 3 — Agents

`agents/base.py` provides the harness: system prompt assembly, tool selection,
tool-call execution, context injection from memory, the turn budget, and the
final-answer coercion when the budget runs out.

The loop is:

```
build messages (system + history + user)
        │
        ├─► generate with tool schemas
        │       │
        │       ├─ no tool calls ──► final answer ──► DONE
        │       │
        │       └─ tool calls ──► execute each ──► append tool results
        │                              │
        │                              └────────────► loop (turn + 1)
        │
        └─ turn budget exhausted ──► force a final answer
```

### The eight agents

`agents/builtin/__init__.py`

Each is a small class declaring its identity and its tools:

```python
class DeepResearchAgent(BaseAgent):
    name = "deep_research"
    mode = "on_demand"
    allowed_tools = ["web_search", "web_fetch", "file_search", "file_read", "file_list"]
    tier = ""          # inherits from config
    system_prompt_extra = (
        "You are the research agent. Break the question into sub-questions ..."
    )
    max_turns = 16
```

### Execution modes

- **on-demand** — triggered by a user request or an API call.
- **scheduled** — cron-fired. `agents/scheduler.py` implements a dependency-free
  cron parser supporting `*`, `*/n`, `a-b`, lists, and the `mon-sun` / `jan-dec`
  aliases. `CronSchedule.next_after()` finds the next fire time.
- **continuous** — `ContinuousLoop` runs an agent on a fixed interval, keeping
  a `LoopState` across cycles and compressing observations so the history stays
  bounded.

---

## Pillar 4 — Tools & Memory

### The tool registry

`tools/registry.py`

Tools declare a JSON schema (hand-written or inferred from the signature plus a
Google-style `Args:` docstring) and a Python callable. Execution is sandboxed by
policy, timeouts, and an allow-list — and every invocation is recorded.

### The file sandbox

`tools/builtin/files.py`

All file access resolves through a `FileSandbox`. Paths are resolved to their
real location *before* the check, so `../../etc/passwd` and Windows drive paths
cannot escape the permitted roots. Windows drive-letter and UNC paths are
recognised as absolute regardless of host OS, so a path can never be silently
relativised into the sandbox.

### The calculator

`tools/builtin/code.py` evaluates arithmetic with a hand-written AST walker —
not `eval`. Only literals, binary/unary operators, comparisons, and a whitelist
of math functions are permitted. Attribute access and imports are rejected.

### Memory

`memory/store.py`

One SQLite database, three tables: `memories` (with an embedded float32 vector),
`conversations` and `sessions`.

Retrieval blends two signals:

```
score = 0.75 × cosine(query_vec, memory_vec) + 0.45 × token_overlap
```

Cosine alone misses exact keyword hits; lexical alone misses paraphrase.
Blending them means both work.

The default embedder is a hashed bag-of-tokens plus character-trigram projection
— deterministic, offline, zero download. Set `embedding_model` to a
SentenceTransformers id and a real encoder is used instead, with no other code
change.

Exact duplicates are collapsed via a normalised-content fingerprint, with tags
merged on repeat writes.

### MCP

`tools/mcp/client.py` — connect to MCP servers over stdio or HTTP. Their tools
are bridged into the registry as `mcp__<server>__<tool>`.

`tools/mcp/server.py` — run C.A.R.L.A. *as* an MCP server, exposing
`carla_ask`, `carla_memory_search`, `carla_memory_store`,
`carla_list_agents` and `carla_telemetry`.

```bash
carla mcp serve
```

Then point Claude Desktop at it:

```json
{
  "mcpServers": {
    "carla": {
      "command": "carla",
      "args": ["mcp", "serve"]
    }
  }
}
```

---

## Pillar 5 — Learning

`learning/traces.py` writes every exchange to SQLite. `learning/optimizer.py`
reads those traces back and produces *proposals* — never silent rewrites.

Three outputs:

1. **Prompt suggestion** — mines structural patterns correlated with high-rated
   responses and anti-patterns correlated with failures, then proposes an
   amended system prompt with a rationale and a confidence score.
2. **Routing policy** — per agent, weights quality (from response structure and
   human rating) against latency to recommend a tier.
3. **Agent advice** — flags error rates, slow requests, unused tools and failing
   tools.

---

## The persona & channel splitter

`core/persona.py`

This is where C.A.R.L.A. stops being a chat wrapper.

### The split

```
raw model text
    │
    ├─ extract fenced code blocks          → code_blocks[]
    │
    ├─ strip inline code, diffs, tables,
    │  URLs from the prose path             → prose
    │
    ├─ detect: is this code-only?          → is_code_only
    │
    └─ condense prose to one tight
       sentence, apply a lead-in,
       strip banned filler                  → speech
```

`display` is always the original text, untouched.

### What's banned

The scrubber removes: `as an AI`, `I cannot`, `I'm sorry`, `I apologize`,
`As a language model`, `Certainly!`, `Of course!`, `Let me know if`,
`Happy to help`, `As always`, `I understand`, `Please note that`,
`It is important to note`, `I hope this helps`, `Great question`.

Zero moralising, zero robotic filler.

### Voice application

The spoken line gets a rotating lead-in (`Right, Boss.` / `Done, Boss.` /
`Understood, Boss.` / `On it, Boss.` / `All set, Boss.`) and is trimmed to a
single sentence so it reads as speech, not as prose being read aloud.

---

## Voice

`voice/`

```
                    ┌──────────────┐
  mic ──► WakeWord ─┤              │
       └─► STT ─────┤  VoiceSystem ├──► ChannelPayload.speech ──► TTS ──► speakers
                    └──────────────┘
```

- **Wake word** (`openWakeWord`): ONNX, offline, tiny. Detects "hey carla".
- **STT** (`faster-whisper`): CTranslate2, `int8` on CPU.
- **TTS** (Kokoro / Piper): both CPU-only, both with Irish voice options.
- **Barge-in**: a background monitor computes RMS energy while C.A.R.L.A. is
  speaking; sustained energy above threshold interrupts playback.

Everything degrades to a no-op rather than crashing if a backend is missing.

---

## Telemetry

`core/telemetry.py`

Three metrics, continuously:

| Metric | Source |
|--------|--------|
| Latency | Wall-clock around each adapter call, with time-to-first-token |
| Tokens/sec | Completion tokens ÷ generation time |
| VRAM / RAM | `nvidia-smi` / `rocm-smi` for VRAM, `psutil` for RAM and CPU |

The sampler runs on a background thread at `sample_interval`. Results land in
SQLite and feed the HUD's telemetry panel, the `/api/telemetry` endpoint and
`carla doctor`.

---

## The HUD

`server/web/`

Vanilla HTML/CSS/JS. No build step, no CDN, no framework — it works offline.

- `carla.css` — every colour, radius, glow and easing is a CSS variable at the
  top. Change `--carla-cyan` and the entire console re-skins.
- `carla.js` — WebSocket client, dependency-free Markdown renderer, the
  animated Voice Core (a canvas-free SVG path animation driven by
  `requestAnimationFrame`).
- `index.html` — the layout.

The WebSocket pushes a full status snapshot on connect, then live deltas:
telemetry samples every second, agent status, voice state, job events, tier
changes.

---

## Testing strategy

- **Unit tests** (`tests/test_core.py`) — 61 tests. Each primitive in
  isolation, plus the persona splitter, cron parser, memory store, sandbox and
  HTTP surface. No model required.
- **Integration** (`tests/e2e_smoke.py`) — 32 live checks against a running
  instance with a mock model: tool-calling loop, dual-channel routing, semantic
  memory, telemetry, cron, tier switching, the OpenAI API, and WebSocket
  delivery.

The suite deliberately includes a test asserting that **no model name appears
in the router source** — the model-agnostic guarantee is enforced, not just
documented.
