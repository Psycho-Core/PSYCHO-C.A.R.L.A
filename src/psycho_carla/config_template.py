"""
PSYCHO-C.A.R.L.A. — Default configuration template.

This is the reference `psycho_carla.toml`. It is fully commented and every
value is safe to edit — including the tier table, which is where you point
C.A.R.L.A. at whatever models you actually run.

Layer: Core.
"""

from __future__ import annotations


TEMPLATE = r'''# =============================================================================
#  PSYCHO-C.A.R.L.A. — Personal AI on Personal Devices
#  Configuration file:  psycho_carla.toml
#
#  MODEL-AGNOSTIC BY DESIGN.
#  Nothing here names a required model. You define *capability tiers* — logical
#  roles like fast_tier / deep_tier / vision_tier — and point each one at
#  whatever provider you happen to run. Swap Ollama for llama.cpp, or a local
#  model for a cloud API, and C.A.R.L.A. does not care.
#
#  Every provider below speaks either the OpenAI API shape or Ollama's native
#  API. The `openai_compat` adapter is the universal escape hatch: point
#  base_url at anything and it works.
# =============================================================================

# -----------------------------------------------------------------------------
# [intelligence] — Pillar 1: the model router
# -----------------------------------------------------------------------------
[intelligence]
# The tier used when nothing else is specified.
default_tier  = "fast_tier"
# Tier to fall back to on failure. Empty = no fallback.
fallback_tier = "deep_tier"

# Global sampling defaults. Individual tiers override these.
temperature         = 0.7
max_tokens          = 2048
top_p               = 0.9
top_k               = 40
repetition_penalty  = 1.0
stop_sequences      = []
context_window      = 8192


# =============================================================================
#  CAPABILITY TIERS
#
#  A tier is a *role*, not a model. Point `fast_tier` at a 3B local model and
#  `deep_tier` at a 70B, and agents can request "fast" or "deep" without ever
#  knowing what that means.
#
#  provider   : ollama | openai_compat | llamacpp | generic_http
#  model      : the provider's model identifier
#  base_url   : endpoint (overrides [engine.adapters.*])
#  api_key_env: name of the environment variable holding your key
#               (e.g. "OPENAI_API_KEY", "OPENROUTER_API_KEY")
# =============================================================================

# --- fast_tier: quick, cheap, good enough for most turns -------------------
[tiers.fast_tier]
provider       = "ollama"
model          = "qwen2.5:7b-instruct"
temperature    = 0.7
max_tokens     = 2048
description    = "Default workhorse. Local Ollama instance."

# --- deep_tier: heavy reasoning, research, difficult synthesis --------------
[tiers.deep_tier]
provider       = "ollama"
model          = "qwen2.5:14b-instruct"
temperature    = 0.4
max_tokens     = 4096
top_p          = 0.92
description    = "Deep reasoning. Research, planning, hard analysis."

# --- vision_tier: multimodal ------------------------------------------------
[tiers.vision_tier]
provider       = "ollama"
model          = "llama3.2-vision:11b"
temperature    = 0.6
max_tokens     = 2048
description    = "Multimodal. Screenshots, images, documents."

# --- native_tier: runs INSIDE C.A.R.L.A., no Ollama needed ---------------
# This tier executes the model in C.A.R.L.A.'s own process on the CPU, so
# there is nothing to install and no daemon to start. Download the model
# first (Model Manager in the HUD, or: carla model pull native:smollm2-360m)
# then point this tier at the folder it landed in.
#
#   [tiers.native_tier]
#   provider    = "native_onnx"
#   model       = "native:smollm2-360m"
#   temperature = 0.7
#   max_tokens  = 1024
#   [tiers.native_tier.extra]
#   model_path  = "~/.psycho_carla/models/native/native_smollm2-360m"
#
# The Model Manager writes this block for you when you activate a native
# model, so you do not normally need to edit it by hand.

# --- cloud_tier: optional escape hatch to a hosted API ---------------------
# Swap the commented block below for a live cloud endpoint. C.A.R.L.A. works
# identically against any OpenAI-compatible API.
#
# [tiers.cloud_tier]
# provider       = "openai_compat"
# model          = "gpt-4o-mini"
# base_url       = "https://api.openai.com/v1"
# api_key_env    = "OPENAI_API_KEY"
# temperature    = 0.7
# max_tokens     = 4096
# description    = "Cloud fallback for when local is not enough."

# --- code_tier: specialised for code tasks ----------------------------------
# [tiers.code_tier]
# provider       = "openai_compat"
# model          = "qwen2.5-coder:7b"
# base_url       = "http://localhost:11434/v1"
# description    = "Code generation and review."


# -----------------------------------------------------------------------------
# [engine] — Pillar 2: the adapter layer
# -----------------------------------------------------------------------------
[engine]
default_adapter = "ollama"
detect_hardware = true
fallback_chain  = ["ollama", "openai_compat"]

[engine.adapters.ollama]
base_url = "http://localhost:11434"
timeout  = 300

[engine.adapters.openai_compat]
base_url = "http://localhost:1234/v1"
timeout  = 300

[engine.adapters.llamacpp]
base_url = "http://localhost:8080"
timeout  = 300

# Generic HTTP escape hatch — no code required to add a provider.
# [engine.adapters.my_backend]
# base_url       = "https://example.local/generate"
# prompt_path    = "input"
# response_path  = "output.text"
# static_fields  = { stream = false }


# -----------------------------------------------------------------------------
# [agent] — Pillar 3: behaviour
# -----------------------------------------------------------------------------
[agent]
default_agent = "orchestrator"
max_turns     = 12
# Comma-separated tool allow-list. Empty = every enabled tool.
tools         = []
objective     = "Deliver accurate, concise answers using the tools available."
context_from_memory = true

# Per-agent overrides. Any field from the agent definition may be overridden.
[agents.orchestrator]
description = "Plans multi-step work, selects tools, delegates to specialists."
max_turns   = 14

[agents.operative]
description = "Continuous autonomous executor with state management."
tier        = "fast_tier"

[agents.deep_research]
description = "Multi-hop research with citations across web and local docs."
tier        = "deep_tier"
max_turns   = 18

[agents.morning_digest]
description = "Daily briefing — spoken, terse, prioritised."
tier        = "fast_tier"
schedule    = "0 7 * * *"

[agents.code_assist]
description = "Code generation, execution and debugging with file I/O."
tier        = "deep_tier"

[agents.monitor_operative]
description = "Long-horizon monitoring with memory and context compression."
tier        = "fast_tier"

[agents.memory_curator]
description = "Maintains the memory store — dedupe, prune, enrich."
tier        = "fast_tier"

[agents.scheduler]
description = "Manages scheduled jobs and dispatches them to specialists."
tier        = "fast_tier"

[agents.simple]
description = "One-shot answer with no tool use."
tier        = "fast_tier"

[agents.native_react]
description = "ReAct reasoning loop with explicit thought/action steps."
tier        = "deep_tier"

[agents.native_openhands]
description = "CodeAct agent — acts by writing, running and fixing code."
tier        = "deep_tier"

[agents.proactive]
description = "Proactive sweep over memory and pending work."
tier        = "fast_tier"

[agents.rlm]
description = "Recursive LM — decomposes, answers sub-questions, synthesises."
tier        = "deep_tier"

[agents.conductor]
description = "Conductor — deep-tier planning, local-tier execution."
tier        = "fast_tier"

[agents.minions]
description = "Minions — splits work across small fast-tier workers."
tier        = "fast_tier"

[agents.advisors]
description = "Advisors — multiple perspectives, one recommendation."
tier        = "deep_tier"

[agents.mini_swe_agent]
description = "Mini SWE agent — reproduce, patch, and verify bugs."
tier        = "deep_tier"

[agents.skillorchestra]
description = "SkillOrchestra — skill-first orchestration."
tier        = "deep_tier"

[agents.toolorchestra]
description = "ToolOrchestra — optimal tool selection and sequencing."
tier        = "deep_tier"


# -----------------------------------------------------------------------------
# [tools] — Pillar 4: tools & memory
# -----------------------------------------------------------------------------
[tools.storage]
default_backend   = "sqlite"
context_top_k     = 5
context_min_score = 0.05
context_max_tokens = 2048
chunk_size        = 1024
chunk_overlap     = 128
vector_dim        = 384
# Set to a SentenceTransformers model id to use real embeddings instead of the
# built-in hashing embedder (zero download, still good recall).
embedding_model   = ""

[tools]
enabled = []
disabled = []

[tools.mcp]
enabled = true
request_timeout = 30
client_info_name = "psycho-carla"

# MCP servers. Each becomes tools named mcp__<server>__<tool>.
# [tools.mcp.servers.filesystem]
# transport = "stdio"
# command   = "npx"
# args      = ["-y", "@modelcontextprotocol/server-filesystem", "D:/notes"]
#
# [tools.mcp.servers.search]
# transport = "stdio"
# command   = "npx"
# args      = ["-y", "@modelcontextprotocol/server-brave-search"]
# env        = { BRAVE_API_KEY = "your-key" }
#
# [tools.mcp.servers.remote]
# transport = "http"
# url       = "http://localhost:3000/mcp"


# -----------------------------------------------------------------------------
# [memory] — persistent memory
# -----------------------------------------------------------------------------
[memory]
enabled          = true
backend          = "local"
extraction_model = ""     # tier alias for fact extraction; empty = default tier
max_facts        = 2000


# -----------------------------------------------------------------------------
# [learning] — Pillar 5: traces & optimisation
# -----------------------------------------------------------------------------
[learning]
enabled         = false
update_interval = 50
auto_update     = false
optimizer       = "dspy"

[learning.routing]
policy      = "heuristic"
min_samples = 10

[learning.intelligence]
policy = "sft"

[learning.agent]
policy                     = "agent_advisor"
advisor_confidence_threshold = 0.8

# Tool-selection optimiser: learns which tools each agent should keep,
# drop and promote. Set policy = "" to disable the surface.
[learning.tools]
policy = "tool_selection"

# Engine-specification optimiser: proposes quantisation, context window,
# batch size and sampling per tier. Set policy = "" to disable.
[learning.engine]
policy = "engine_spec"

# Objective weights used by the routing and engine optimisers.
[learning.metrics]
accuracy_weight   = 0.6
latency_weight    = 0.2
cost_weight       = 0.1
efficiency_weight = 0.1


# -----------------------------------------------------------------------------
# [traces] — interaction trace logger
# -----------------------------------------------------------------------------
[traces]
enabled     = true
retain_days = 14


# -----------------------------------------------------------------------------
# [telemetry] — efficiency telemetry
# -----------------------------------------------------------------------------
[telemetry]
enabled              = true
track_latency        = true
track_tokens_per_second = true
track_resources      = true
# Energy + FLOPs are first-class efficiency constraints alongside latency
# and cost. `energy_vendor` picks the power probe:
#   auto | nvidia | amd | apple | cpu_rapl | cpu | none
# "cpu" is a TDP estimate, not a measurement — useful when no probe exists.
track_energy         = true
energy_vendor        = "auto"
gpu_metrics          = true
gpu_poll_interval_ms = 50
warmup_samples       = 0
steady_state_window  = 5
steady_state_threshold = 0.05
sample_interval      = 1.0
retain_days          = 30


# -----------------------------------------------------------------------------
# [server] — HUD + OpenAI-compatible API
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# [skills] — agentskills.io catalog
# -----------------------------------------------------------------------------
# A skill is a folder with a SKILL.md file: YAML frontmatter (name +
# description) followed by Markdown instructions. Install with:
#   carla skill install <path-or-url>
[skills]
enabled        = true
# Empty = <home>/skills
dir            = ""
# Inject each skill's name + description into the agent's prompt, and let
# the agent pull the full body on demand (progressive disclosure).
auto_load      = true
max_skills     = 0
validate_on_load = true


# -----------------------------------------------------------------------------
# [sessions] — cross-channel conversations
# -----------------------------------------------------------------------------
# One conversation, identified independently of where it came from. The same
# person on the HUD and on the CLI shares a session when their channel ids
# are linked. Long sessions are consolidated into anchors; idle ones expire.
[sessions]
enabled          = true
# Empty = <home>/sessions.db
db_path          = ""
# Sessions idle for longer than this are eligible for expiry.
max_age_hours    = 24.0
# Consolidate a session once it grows past this many messages.
consolidation_threshold = 100
max_message_tokens = 4000


# -----------------------------------------------------------------------------
# [server] — the HUD, the OpenAI-compatible API and A2A
# -----------------------------------------------------------------------------
[server]
host              = "127.0.0.1"
port              = 8787
agent             = "orchestrator"
workers           = 1
cors_origins      = ["*"]
openai_compat_prefix = "/v1"

# An exposed agent carries shell and file tools, so the server refuses to
# bind a non-loopback address without a key. Generate one with:
#   carla auth generate-key
# then export it as PSYCHO_CARLA_API_KEY (or point api_key_env elsewhere).
api_key_env       = "PSYCHO_CARLA_API_KEY"
api_key           = ""
require_auth      = true

# A2A — the Agent-to-Agent protocol. Publishes a discovery card at
# /.well-known/agent.json and accepts tasks at POST /a2a/tasks.
a2a_enabled       = true
a2a_agent         = "PSYCHO-C.A.R.L.A."
a2a_token         = ""              # blank = reuse the server's API key


# -----------------------------------------------------------------------------
# [cron] — scheduled jobs
# -----------------------------------------------------------------------------
# Declare one or more jobs; they are registered with the scheduler when the
# runtime starts. The expression is standard 5-field cron.
#   [[cron]]
#   name = "morning-digest"
#   schedule = "30 7 * * *"
#   agent = "digest"
#   prompt = "Brief me: overnight messages, today's calendar."
[cron]
enabled = true


# -----------------------------------------------------------------------------
# [voice] — CPU-only dual-channel voice
#
#  Channel split: C.A.R.L.A. speaks a concise summary; raw code, diffs and
#  tables are routed to the HUD verbatim. Both run entirely on CPU.
# -----------------------------------------------------------------------------
[voice]
enabled          = true
tts_engine       = "kokoro"        # kokoro | piper | silent
tts_voice        = "irish_female"  # irish_female | irish_male | female | male
                                # NOTE: no Irish (en_IE) voice model exists in
                                # Piper or Kokoro. The `irish_*` aliases resolve
                                # to the closest British English voices
                                # (kokoro bf_emma / bm_george). Run
                                # `carla voice audition` to compare them.
tts_model        = ""              # explicit voice id / .onnx path
tts_model_path   = ""              # local Kokoro checkpoint dir (skips the ~315MB download)
tts_speed        = 1.0
tts_device       = "cpu"

stt_engine       = "faster-whisper"  # faster-whisper | whisper | silent
stt_model        = "base.en"
stt_device       = "cpu"
stt_compute_type = "int8"

wake_word         = "hey carla"
wake_word_enabled = true
wake_word_engine  = "openwakeword"

barge_in        = true
channel_split   = true
speak_min_chars = 12
listen_timeout  = 30.0


# -----------------------------------------------------------------------------
# [persona] — C.A.R.L.A.'s voice
# -----------------------------------------------------------------------------
[persona]
name            = "C.A.R.L.A."
# What C.A.R.L.A. calls you. Leave it blank and she will call you "Psycho".
# Set it to anything you like — e.g. user_name = "Alex" — and she will use
# that name in chat, on your channels, in the HUD and when speaking.
user_name       = ""
style           = "sharp, loyal, dry-witted"
irish           = true
max_spoken_chars = 480


# -----------------------------------------------------------------------------
# [web_search]
# -----------------------------------------------------------------------------
[web_search]
provider    = "duckduckgo"   # duckduckgo | brave | searxng | tavily | none
api_key_env = ""            # e.g. BRAVE_API_KEY
base_url    = ""            # for searxng
max_results = 5
timeout     = 15.0


# -----------------------------------------------------------------------------
# [security]
# -----------------------------------------------------------------------------
[security]
allow_shell      = true
shell_timeout    = 60.0
# Directories the file tools may touch. Empty = your home directory.
sandbox_paths    = []
# If non-empty, only these command names may be run by shell_exec.
allowed_commands = []
# Scanner / policy depth (psycho_carla.security). "log" records findings,
# "block" refuses flagged input, "redact" masks them.
scan_input       = false
scan_output      = false
mode             = "log"
secret_scanner   = true
pii_scanner      = true
merkle_audit     = false
ssrf_protection  = true
rate_limit_enabled = false
rate_limit_rpm   = 120
rate_limit_burst = 20
default_deny     = false
capabilities_policy_path = ""
signing_key_path = ""


# -----------------------------------------------------------------------------
# [analytics] — local-first. Nothing is sent anywhere unless host+key are set.
# -----------------------------------------------------------------------------
[analytics]
enabled = false
host    = ""
key     = ""


# -----------------------------------------------------------------------------
# [sandbox] — isolated code execution (docker | wasm | subprocess)
# -----------------------------------------------------------------------------
[sandbox]
enabled = false
runtime = "docker"
image   = "python:3.12-slim"
timeout = 60.0
max_concurrent = 2
wasm_memory_limit_mb = 256


# -----------------------------------------------------------------------------
# [workflow] / [operators]
# -----------------------------------------------------------------------------
[workflow]
enabled = true
max_parallel = 4
default_node_timeout = 300.0

[operators]
enabled = true
manifests_dir = ""
auto_activate = false


# -----------------------------------------------------------------------------
# [system_prompt] / [memory_files] — SOUL / MEMORY / USER prompt files
# -----------------------------------------------------------------------------
[system_prompt]
prefix = ""
soul_max_chars = 2000
memory_max_chars = 2000
user_max_chars = 1500
skill_desc_max_chars = 200
truncation_strategy = "tail"

[memory_files]
soul_path = ""
memory_path = ""
user_path = ""
persona_name = ""


# -----------------------------------------------------------------------------
# [compression] — session history compression
# -----------------------------------------------------------------------------
[compression]
enabled = true
threshold = 0.75
strategy = "summarise"


# -----------------------------------------------------------------------------
# [digest] / [proactive]
# -----------------------------------------------------------------------------
[digest]
enabled = false
schedule = "0 7 * * *"
honorific = "Psycho"
voice_speed = 1.0

[proactive]
enabled = false
schedule = "0 */4 * * *"
hours_back = 24
notification_channel = ""


# -----------------------------------------------------------------------------
# [optimize] / [agent_manager] / [deep_research] / [mining]
# -----------------------------------------------------------------------------
[optimize]
max_trials = 10
early_stop_patience = 3
max_samples = 50

[agent_manager]
enabled = true

[deep_research]
engine = ""
model = ""

[mining]
enabled = false
pool = ""
wallet = ""


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
log_level = "INFO"
'''


def render_default_config() -> str:
    """Return the default configuration text."""
    return TEMPLATE
