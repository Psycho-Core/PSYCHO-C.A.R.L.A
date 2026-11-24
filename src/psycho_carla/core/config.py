"""
PSYCHO-C.A.R.L.A. — Core Configuration System.

Model-agnostic by design. Nothing in this module knows about a specific model
name; every model reference is a *tier alias* (fast_tier, deep_tier, vision_tier)
that resolves through the [tiers] table in psycho_carla.toml.

Layer: Core (shared by all five primitives).
"""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Dict

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore

APP_NAME = "PSYCHO-C.A.R.L.A."
APP_SLUG = "psycho_carla"
CONFIG_FILENAME = "psycho_carla.toml"


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #
def _windows_default_home() -> Path:
    """Return the platform-appropriate state directory for PSYCHO-C.A.R.L.A."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "PsychoCarla"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "psycho_carla"
    return Path.home() / ".psycho_carla"


def default_home() -> Path:
    override = os.environ.get("PSYCHO_CARLA_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return _windows_default_home()


def default_config_path() -> Path:
    override = os.environ.get("PSYCHO_CARLA_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return default_home() / CONFIG_FILENAME


# --------------------------------------------------------------------------- #
# Dataclass-backed config sections
# --------------------------------------------------------------------------- #
@dataclass
class TierConfig:
    """A capability tier alias -> a concrete model routing rule."""

    provider: str = "ollama"          # adapter name in providers/adapters
    model: str = ""                  # provider-specific model identifier
    base_url: str = ""               # override endpoint (if the adapter needs one)
    api_key_env: str = ""            # env var holding the credential
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 0.9
    top_k: int = 40
    repetition_penalty: float = 1.0
    stop_sequences: list[str] = field(default_factory=list)
    timeout: float = 300.0
    extra: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def merged_with(self, other: "TierConfig") -> "TierConfig":
        """Return a copy of self with any explicitly-set field of `other` applied."""
        out = copy.deepcopy(self)
        for f in fields(other):
            val = getattr(other, f.name)
            if f.name in ("description",):
                if val:
                    out.description = val
                continue
            if val in (None, "", [], {}, 0, 0.0) and f.name not in (
                "temperature",
                "max_tokens",
                "top_p",
                "top_k",
                "repetition_penalty",
                "timeout",
            ):
                continue
            if f.name == "extra":
                merged = dict(out.extra)
                merged.update(val)
                out.extra = merged
            else:
                setattr(out, f.name, val)
        return out


@dataclass
class IntelligenceConfig:
    """Pillar 1 — model directory / router."""

    default_tier: str = "fast_tier"
    fallback_tier: str = ""
    temperature: float = 0.7
    max_tokens: int = 2048
    top_p: float = 0.9
    top_k: int = 40
    repetition_penalty: float = 1.0
    stop_sequences: list[str] = field(default_factory=list)
    context_window: int = 8192
    # Tier aliases -> TierConfig. Populated from [tiers.*] at load time.
    tiers: dict[str, TierConfig] = field(default_factory=dict)
    # Optional global provider fallbacks (legacy-style config for parity).
    default_model: str = ""
    fallback_model: str = ""
    provider: str = ""
    quantization: str = ""


@dataclass
class EngineConfig:
    """Pillar 2 — inference runtime / adapter layer."""

    default_adapter: str = "ollama"
    adapters: dict[str, dict[str, Any]] = field(default_factory=dict)
    detect_hardware: bool = True
    fallback_chain: list[str] = field(default_factory=list)


@dataclass
class CronJobConfig:
    """One scheduled job declared in the configuration file."""

    name: str = ""
    schedule: str = ""
    agent: str = "orchestrator"
    prompt: str = ""
    tier: str = ""
    speak: bool = True
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "schedule": self.schedule,
            "agent": self.agent,
            "prompt": self.prompt,
            "tier": self.tier,
            "speak": self.speak,
            "enabled": self.enabled,
        }


@dataclass
class CronConfig:
    """Scheduled jobs declared in ``[[cron]]`` blocks."""

    enabled: bool = True
    jobs: list[CronJobConfig] = field(default_factory=list)


@dataclass
class AgentConfig:
    """Pillar 3 — behaviour layer."""

    default_agent: str = "orchestrator"
    max_turns: int = 10
    tools: list[str] = field(default_factory=list)
    objective: str = ""
    system_prompt: str = ""
    system_prompt_path: str = ""
    context_from_memory: bool = True
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class StorageConfig:
    """Pillar 4 — memory storage backend."""

    default_backend: str = "sqlite"
    context_top_k: int = 5
    context_min_score: float = 0.05
    context_max_tokens: int = 2048
    chunk_size: int = 1024
    chunk_overlap: int = 128
    vector_dim: int = 384
    embedding_model: str = ""


@dataclass
class MCPConfig:
    enabled: bool = True
    servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_timeout: float = 30.0
    client_info_name: str = "psycho-carla"


@dataclass
class BrowserToolConfig:
    headless: bool = True
    timeout_ms: int = 30000
    viewport_width: int = 1280
    viewport_height: int = 800


@dataclass
class WeatherToolConfig:
    provider: str = "open-meteo"
    default_location: str = "Adelaide"
    units: str = "metric"
    lang: str = "en"


@dataclass
class ToolsConfig:
    storage: StorageConfig = field(default_factory=StorageConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    enabled: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    shell: dict[str, Any] = field(default_factory=dict)
    browser: BrowserToolConfig = field(default_factory=BrowserToolConfig)
    weather: WeatherToolConfig = field(default_factory=WeatherToolConfig)
    web_search: dict[str, Any] = field(default_factory=dict)
    file_ops: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryConfig:
    enabled: bool = True
    backend: str = "local"
    extraction_model: str = ""
    max_facts: int = 2000
    db_path: str = ""


@dataclass
class LearningConfig:
    enabled: bool = False
    update_interval: int = 50
    auto_update: bool = False
    routing: dict[str, Any] = field(default_factory=dict)
    intelligence: dict[str, Any] = field(default_factory=dict)
    agent: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    optimizer: str = "dspy"
    # Per-surface policy switches. Each surface names the optimiser to use
    # for it; set a surface to "" (or "none") to disable that surface.
    #   routing     — learn which tier serves which agent
    #   intelligence— learn prompt / model specification
    #   agent       — learn tool selection and agentic logic
    #   tools       — learn tool selection (alias of agent.tools)
    #   engine      — learn engine specification (quantisation, ctx, batch)
    tool_selection: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, Any] = field(default_factory=dict)
    engine: dict[str, Any] = field(default_factory=dict)
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "accuracy": 0.6,
            "latency": 0.2,
            "cost": 0.1,
            "efficiency": 0.1,
        }
    )

    def policy_for(self, surface: str) -> str:
        """Return the optimiser policy for a learning surface.

        An empty string means the surface is disabled. The ``tools``
        surface falls back to ``tool_selection`` so that either spelling
        works in the TOML.
        """
        surface = (surface or "").lower()
        section = getattr(self, surface, None)
        if not isinstance(section, dict):
            section = {}
        # Aliases: either spelling of the same surface is honoured.
        if surface == "tools" and not section.get("policy"):
            alias = getattr(self, "tool_selection", None)
            if isinstance(alias, dict):
                section = alias
        policy = str(section.get("policy", "") or "").strip().lower()
        if policy in ("none", "off", "disabled", "false"):
            return ""
        return policy

    def surface_enabled(self, surface: str) -> bool:
        """Is a learning surface switched on?"""
        return bool(self.policy_for(surface))


@dataclass
class TelemetryConfig:
    enabled: bool = True
    track_latency: bool = True
    track_tokens_per_second: bool = True
    track_resources: bool = True
    track_energy: bool = True
    energy_vendor: str = "auto"   # auto | nvidia | amd | apple | cpu_rapl | cpu | none
    gpu_metrics: bool = True
    gpu_poll_interval_ms: int = 50
    warmup_samples: int = 0
    steady_state_window: int = 5
    steady_state_threshold: float = 0.05
    sample_interval: float = 1.0
    db_path: str = ""
    retain_days: int = 30


@dataclass
class TracesConfig:
    enabled: bool = True
    db_path: str = ""
    retain_days: int = 14


@dataclass
class SessionsConfig:
    """Cross-channel session settings."""

    enabled: bool = True
    db_path: str = ""
    max_age_hours: float = 24.0
    consolidation_threshold: int = 100
    max_message_tokens: int = 4000


@dataclass
class ChannelsConfig:
    """Messaging channels.

    Each key is a channel id (``telegram``, ``slack``, ``email_channel``…)
    and each value is that channel's options. Secrets should live in the
    encrypted vault rather than here; the config holds the reference.
    """
    enabled: bool = True
    #: Per-channel options, keyed by channel id.
    channels: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    #: The agent used when a conversation names no override.
    default_agent: str = ""
    #: The longest inbound message that will be processed.
    max_length: int = 8000


@dataclass
class SkillsConfig:
    """Skills catalog settings (agentskills.io format)."""

    enabled: bool = True
    dir: str = ""            # empty -> <home>/skills
    auto_load: bool = True   # inject skill metadata into the agent prompt
    max_skills: int = 0      # 0 = no cap
    validate_on_load: bool = True
    # Bundled library: copy the 20 packaged step-skills into the skills
    # dir on first use so they appear in the catalog like installed ones.
    install_bundled: bool = True
    # Remote skill sources (index-synced catalogs of skill URLs).
    sources: list[str] = field(default_factory=list)


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8787
    agent: str = "orchestrator"
    api_key_env: str = "PSYCHO_CARLA_API_KEY"
    api_key: str = ""
    require_auth: bool = True
    # A2A (agent-to-agent) — lets other agents call C.A.R.L.A.
    a2a_enabled: bool = True
    a2a_agent: str = ""
    a2a_token: str = ""
    model: str = ""
    workers: int = 1
    cors_origins: list[str] = field(default_factory=lambda: ["*"])
    openai_compat_prefix: str = "/v1"


@dataclass
class VoiceConfig:
    """Dual-channel CPU-only voice I/O."""

    enabled: bool = True
    tts_engine: str = "kokoro"          # kokoro | piper | silent
    tts_voice: str = "irish_female"     # human-readable alias, resolved by the TTS backend
    tts_model: str = ""                 # explicit model path/ID if required
    tts_model_path: str = ""            # local Kokoro checkpoint dir (avoids re-download)
    tts_speed: float = 1.0
    tts_device: str = "cpu"
    stt_engine: str = "faster-whisper"  # faster-whisper | whisper | silent
    stt_model: str = "base.en"
    stt_device: str = "cpu"
    stt_compute_type: str = "int8"
    wake_word: str = "hey carla"
    wake_word_enabled: bool = True
    wake_word_engine: str = "openwakeword"
    barge_in: bool = True
    channel_split: bool = True
    speak_min_chars: int = 12           # below this, don't bother vocalising
    voice_id: str = ""                  # explicit voice id (Kokoro voice name / Piper model)
    listen_timeout: float = 30.0


@dataclass
class PersonaConfig:
    """C.A.R.L.A. voice tuning."""

    name: str = "C.A.R.L.A."
    #: What C.A.R.L.A. calls the user. Blank means she uses ``user_title``
    #: if that is set, and otherwise falls back to "Psycho".
    user_name: str = ""
    #: Legacy alias for ``user_name`` kept so older configs keep working.
    user_title: str = ""
    style: str = "sharp, loyal, dry-witted"
    irish: bool = True
    max_spoken_chars: int = 480
    speak_markdown_tables: bool = False

    @property
    def address_name(self) -> str:
        """The name C.A.R.L.A. addresses the user by ("Psycho" if unset)."""
        return (self.user_name or self.user_title or "Psycho").strip() or "Psycho"


@dataclass
class WebSearchConfig:
    provider: str = "duckduckgo"   # duckduckgo | brave | searxng | tavily | none
    api_key_env: str = ""
    base_url: str = ""
    max_results: int = 5
    timeout: float = 15.0


@dataclass
class SecurityConfig:
    allow_shell: bool = True
    shell_timeout: float = 60.0
    sandbox_paths: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    redact_keys: bool = True
    # Upstream-depth security surface (see psycho_carla.security).
    enabled: bool = True
    scan_input: bool = False
    scan_output: bool = False
    mode: str = "log"  # log | block | redact
    secret_scanner: bool = True
    pii_scanner: bool = True
    audit_log_path: str = ""
    enforce_tool_confirmation: bool = False
    merkle_audit: bool = False
    signing_key_path: str = ""
    ssrf_protection: bool = True
    rate_limit_enabled: bool = False
    rate_limit_rpm: int = 120
    rate_limit_burst: int = 20
    profile: str = ""
    allowed_dirs: list[str] = field(default_factory=list)
    vault_key_path: str = ""
    capabilities_policy_path: str = ""
    default_deny: bool = False


@dataclass
class AnalyticsConfig:
    """Local-first analytics. Nothing leaves the machine unless a host and
    key are explicitly configured."""

    enabled: bool = False
    choice_recorded: bool = False
    host: str = ""
    key: str = ""
    anon_id_path: str = ""
    flush_interval_seconds: int = 30
    flush_at_size: int = 50


@dataclass
class SandboxConfig:
    enabled: bool = False
    image: str = "python:3.12-slim"
    timeout: float = 60.0
    workspace: str = ""
    mount_allowlist_path: str = ""
    max_concurrent: int = 2
    runtime: str = "docker"  # docker | wasm | subprocess
    wasm_fuel_limit: int = 0
    wasm_memory_limit_mb: int = 256


@dataclass
class WorkflowConfig:
    enabled: bool = True
    max_parallel: int = 4
    default_node_timeout: float = 300.0


@dataclass
class OperatorsConfig:
    enabled: bool = True
    manifests_dir: str = ""
    auto_activate: bool = False


@dataclass
class SystemPromptConfig:
    prefix: str = ""
    soul_max_chars: int = 2000
    memory_max_chars: int = 2000
    user_max_chars: int = 1500
    skill_desc_max_chars: int = 200
    truncation_strategy: str = "tail"  # tail | head | middle


@dataclass
class MemoryFilesConfig:
    soul_path: str = ""
    memory_path: str = ""
    user_path: str = ""
    nudge_interval: int = 1800
    persona_name: str = ""


@dataclass
class CompressionConfig:
    enabled: bool = True
    threshold: float = 0.75
    strategy: str = "summarise"


@dataclass
class DigestConfig:
    enabled: bool = False
    schedule: str = "0 7 * * *"
    timezone: str = ""
    persona: str = ""
    honorific: str = "Psycho"
    voice_id: str = ""
    voice_speed: float = 1.0
    tts_backend: str = ""
    sections: list[str] = field(default_factory=list)
    optional_sections: list[str] = field(default_factory=list)


@dataclass
class ProactiveConfig:
    enabled: bool = False
    schedule: str = "0 */4 * * *"
    hours_back: int = 24
    timezone: str = ""
    notification_channel: str = ""


@dataclass
class OptimizeConfig:
    max_trials: int = 10
    early_stop_patience: int = 3
    optimizer_model: str = ""
    optimizer_provider: str = ""
    benchmark: str = ""
    max_samples: int = 50
    judge_model: str = ""
    db_path: str = ""


@dataclass
class AgentManagerConfig:
    enabled: bool = True
    db_path: str = ""


@dataclass
class DeepResearchConfig:
    engine: str = ""
    model: str = ""


@dataclass
class MiningConfig:
    enabled: bool = False
    pool: str = ""
    wallet: str = ""
    worker_name: str = ""


@dataclass
class CarlaConfig:
    """Root configuration object."""

    home: Path = field(default_factory=default_home)
    config_path: Path = field(default_factory=default_config_path)
    intelligence: IntelligenceConfig = field(default_factory=IntelligenceConfig)
    engine: EngineConfig = field(default_factory=EngineConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    telemetry: TelemetryConfig = field(default_factory=TelemetryConfig)
    traces: TracesConfig = field(default_factory=TracesConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)
    cron: CronConfig = field(default_factory=CronConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    persona: PersonaConfig = field(default_factory=PersonaConfig)
    web_search: WebSearchConfig = field(default_factory=WebSearchConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    workflow_config: WorkflowConfig = field(default_factory=WorkflowConfig)
    operators: OperatorsConfig = field(default_factory=OperatorsConfig)
    system_prompt: SystemPromptConfig = field(default_factory=SystemPromptConfig)
    memory_files: MemoryFilesConfig = field(default_factory=MemoryFilesConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    digest: DigestConfig = field(default_factory=DigestConfig)
    proactive: ProactiveConfig = field(default_factory=ProactiveConfig)
    optimize: OptimizeConfig = field(default_factory=OptimizeConfig)
    agent_manager: AgentManagerConfig = field(default_factory=AgentManagerConfig)
    deep_research: DeepResearchConfig = field(default_factory=DeepResearchConfig)
    mining: MiningConfig = field(default_factory=MiningConfig)
    log_level: str = "INFO"
    raw: dict[str, Any] = field(default_factory=dict)

    # -- derived paths ---------------------------------------------------- #
    @property
    def data_dir(self) -> Path:
        p = self.home / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def models_dir(self) -> Path:
        p = self.home / "models"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def memory_db(self) -> Path:
        if self.memory.db_path:
            return Path(self.memory.db_path).expanduser()
        return self.data_dir / "memory.db"

    @property
    def telemetry_db(self) -> Path:
        if self.telemetry.db_path:
            return Path(self.telemetry.db_path).expanduser()
        return self.data_dir / "telemetry.db"

    @property
    def traces_db(self) -> Path:
        if self.traces.db_path:
            return Path(self.traces.db_path).expanduser()
        return self.data_dir / "traces.db"

    @property
    def skills_dir(self) -> Path:
        """Where the skills catalog lives."""
        if self.skills.dir:
            return Path(self.skills.dir).expanduser()
        return self.home / "skills"

    @property
    def sessions_db(self) -> Path:
        """Where the cross-channel session store lives."""
        if self.sessions.db_path:
            return Path(self.sessions.db_path).expanduser()
        return self.data_dir / "sessions.db"


# --------------------------------------------------------------------------- #
# TOML -> dataclass hydration
# --------------------------------------------------------------------------- #
def _assign(section: Any, data: dict[str, Any]) -> None:
    """Assign matching keys from a TOML dict onto a dataclass instance."""
    if not isinstance(data, dict):
        return
    known = {f.name: f for f in fields(section)}
    for key, val in data.items():
        if key not in known:
            continue
        current = getattr(section, key)
        ftype = known[key].type
        # Handle Optional/list typing loosely via runtime isinstance checks.
        if isinstance(current, bool) and isinstance(val, bool):
            setattr(section, key, val)
        elif isinstance(current, int) and not isinstance(current, bool) and isinstance(val, (int, float)):
            setattr(section, key, int(val))
        elif isinstance(current, float) and isinstance(val, (int, float)):
            setattr(section, key, float(val))
        elif isinstance(current, list) and isinstance(val, (list, tuple)):
            setattr(section, key, list(val))
        elif isinstance(current, dict) and isinstance(val, dict):
            setattr(section, key, val)
        elif isinstance(current, str) and isinstance(val, (str, int, float)):
            setattr(section, key, str(val))
        else:
            setattr(section, key, val)


def _coerce_tiers(data: dict[str, Any]) -> dict[str, TierConfig]:
    tiers: dict[str, TierConfig] = {}
    for name, raw in (data or {}).items():
        if not isinstance(raw, dict):
            continue
        tier = TierConfig()
        _assign(tier, raw)
        # anything unrecognised lands in `extra` so backends can read custom knobs
        known = {f.name for f in fields(TierConfig)}
        tier.extra.update({k: v for k, v in raw.items() if k not in known})
        tiers[name] = tier
    return tiers


_SECTION_MAP = {
    "intelligence": IntelligenceConfig,
    "engine": EngineConfig,
    "agent": AgentConfig,
    "tools": ToolsConfig,
    "memory": MemoryConfig,
    "learning": LearningConfig,
    "telemetry": TelemetryConfig,
    "traces": TracesConfig,
    "sessions": SessionsConfig,
    "channels": ChannelsConfig,
    "cron": CronConfig,
    "skills": SkillsConfig,
    "server": ServerConfig,
    "voice": VoiceConfig,
    "persona": PersonaConfig,
    "web_search": WebSearchConfig,
    "security": SecurityConfig,
    "analytics": AnalyticsConfig,
    "sandbox": SandboxConfig,
    "operators": OperatorsConfig,
    "system_prompt": SystemPromptConfig,
    "memory_files": MemoryFilesConfig,
    "compression": CompressionConfig,
    "digest": DigestConfig,
    "proactive": ProactiveConfig,
    "optimize": OptimizeConfig,
    "agent_manager": AgentManagerConfig,
    "deep_research": DeepResearchConfig,
    "mining": MiningConfig,
    "workflow": WorkflowConfig,
}

_NESTED_MAP = {
    ("tools", "storage"): StorageConfig,
    ("tools", "mcp"): MCPConfig,
    ("tools", "browser"): BrowserToolConfig,
    ("tools", "weather"): WeatherToolConfig,
}

# Learning surfaces. Each has a `policy` switch naming the optimiser used
# for that surface; an empty string disables the surface.
LEARNING_SURFACES = (
    "routing",
    "intelligence",
    "agent",
    "tools",
    "engine",
)


def config_from_dict(data: dict[str, Any], config_path: Path | None = None) -> CarlaConfig:
    """Build a fully-populated CarlaConfig from a plain dict (parsed TOML)."""
    cfg = CarlaConfig()
    if config_path is not None:
        cfg.config_path = Path(config_path)
    cfg.raw = copy.deepcopy(data)

    _ATTR_OVERRIDES = {"workflow": "workflow_config"}
    for section_name, klass in _SECTION_MAP.items():
        raw_section = data.get(section_name)
        if not isinstance(raw_section, dict):
            continue
        section = getattr(cfg, _ATTR_OVERRIDES.get(section_name, section_name))
        # Skip keys that belong to a nested dataclass — they are handled below
        # so the nested dataclass instance is not clobbered by a raw dict.
        nested_keys = {child for parent, child in _NESTED_MAP if parent == section_name}
        _assign(section, {k: v for k, v in raw_section.items() if k not in nested_keys})

    # nested sections
    for (parent, child), klass in _NESTED_MAP.items():
        raw = data.get(parent, {}).get(child) if isinstance(data.get(parent), dict) else None
        if isinstance(raw, dict):
            _assign(getattr(getattr(cfg, parent), child), raw)

    # intelligence.tiers from [tiers.*] (canonical) or [intelligence.tiers.*]
    tiers_raw = data.get("tiers")
    if not isinstance(tiers_raw, dict):
        intel_raw = data.get("intelligence")
        if isinstance(intel_raw, dict):
            tiers_raw = intel_raw.get("tiers")
    cfg.intelligence.tiers = _coerce_tiers(tiers_raw or {})

    # Apply global intelligence sampling defaults onto tiers that don't override.
    for tier in cfg.intelligence.tiers.values():
        tier.temperature = tier.temperature if tier.temperature else cfg.intelligence.temperature
        tier.max_tokens = tier.max_tokens or cfg.intelligence.max_tokens
        tier.top_p = tier.top_p or cfg.intelligence.top_p
        tier.top_k = tier.top_k or cfg.intelligence.top_k
        tier.repetition_penalty = (
            tier.repetition_penalty or cfg.intelligence.repetition_penalty
        )
        if not tier.stop_sequences:
            tier.stop_sequences = list(cfg.intelligence.stop_sequences)

    # agents table: [agents.<name>]
    agents_raw = data.get("agents")
    if isinstance(agents_raw, dict):
        cfg.agent.agents = {k: v for k, v in agents_raw.items() if isinstance(v, dict)}

    # channels table: [channels.<name>]
    # The section itself carries the settings; each nested table is one
    # channel's options.
    channels_raw = data.get("channels")
    if isinstance(channels_raw, dict):
        options: dict[str, dict[str, Any]] = {}
        for key, value in channels_raw.items():
            if key in ("enabled", "default_agent", "max_length"):
                continue
            if isinstance(value, dict):
                options[key] = dict(value)
        cfg.channels.channels = options

    # scheduled jobs: [[cron]] blocks, or [cron].jobs
    cron_raw = data.get("cron")
    jobs: list[CronJobConfig] = []
    if isinstance(cron_raw, list):
        jobs.extend(
            CronJobConfig(
                name=str(item.get("name", "")),
                schedule=str(item.get("schedule", "")),
                agent=str(item.get("agent", "orchestrator") or "orchestrator"),
                prompt=str(item.get("prompt", "")),
                tier=str(item.get("tier", "") or ""),
                speak=bool(item.get("speak", True)),
                enabled=bool(item.get("enabled", True)),
            )
            for item in cron_raw
            if isinstance(item, dict) and item.get("schedule")
        )
    elif isinstance(cron_raw, dict):
        cfg.cron.enabled = bool(cron_raw.get("enabled", True))
        for item in cron_raw.get("jobs", []) or []:
            if not isinstance(item, dict) or not item.get("schedule"):
                continue
            jobs.append(
                CronJobConfig(
                    name=str(item.get("name", "")),
                    schedule=str(item.get("schedule", "")),
                    agent=str(item.get("agent", "orchestrator") or "orchestrator"),
                    prompt=str(item.get("prompt", "")),
                    tier=str(item.get("tier", "") or ""),
                    speak=bool(item.get("speak", True)),
                    enabled=bool(item.get("enabled", True)),
                )
            )
    if jobs:
        cfg.cron.jobs = jobs

    # engine adapters table: [engine.adapters.<name>] or [engine.<name>]
    eng_raw = data.get("engine")
    if isinstance(eng_raw, dict):
        adapters = eng_raw.get("adapters")
        if isinstance(adapters, dict):
            cfg.engine.adapters = adapters
        else:
            cfg.engine.adapters = {
                k: v for k, v in eng_raw.items()
                if k not in ("default", "default_adapter", "detect_hardware", "fallback_chain")
                and isinstance(v, dict)
            }

    # legacy compatibility: [memory].context_injection -> agent.context_from_memory
    mem_raw = data.get("memory")
    if isinstance(mem_raw, dict) and "context_injection" in mem_raw:
        cfg.agent.context_from_memory = bool(mem_raw["context_injection"])

    # legacy compatibility: [intelligence].default_model -> fast tier
    if cfg.intelligence.default_model and cfg.intelligence.tiers:
        fast = cfg.intelligence.tiers.get(cfg.intelligence.default_tier)
        if fast and not fast.model:
            fast.model = cfg.intelligence.default_model
    if cfg.intelligence.provider and cfg.intelligence.tiers:
        fast = cfg.intelligence.tiers.get(cfg.intelligence.default_tier)
        if fast and not fast.provider:
            fast.provider = cfg.intelligence.provider

    return cfg


def load_config(path: str | Path | None = None) -> CarlaConfig:
    """Load configuration from disk, falling back to defaults when absent."""
    cfg_path = Path(path).expanduser() if path else default_config_path()
    if not cfg_path.exists():
        return config_from_dict({}, cfg_path)
    with cfg_path.open("rb") as fh:
        data = tomllib.load(fh)
    return config_from_dict(data, cfg_path)


def save_config(cfg: CarlaConfig, path: str | Path | None = None) -> Path:
    """Serialise the config back to TOML. Minimal writer (stdlib has no TOML writer)."""
    target = Path(path).expanduser() if path else cfg.config_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dump_toml(cfg), encoding="utf-8")
    return target


# --------------------------------------------------------------------------- #
# TOML writer
# --------------------------------------------------------------------------- #
def _fmt_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_fmt_value(x) for x in v) + "]"
    if isinstance(v, dict):
        inner = ", ".join(f"{k} = {_fmt_value(val)}" for k, val in v.items())
        return "{" + inner + "}"
    return '"' + str(v) + '"'


def _dump_section(name: str, obj: Any, lines: list[str]) -> None:
    lines.append(f"[{name}]")
    for f in fields(obj):
        val = getattr(obj, f.name)
        if isinstance(val, dict):
            continue
        lines.append(f"{f.name} = {_fmt_value(val)}")
    lines.append("")


def dump_toml(cfg: CarlaConfig) -> str:
    """Render a CarlaConfig to a TOML string."""
    lines: list[str] = [
        "# PSYCHO-C.A.R.L.A. configuration",
        "# Model-agnostic: point [tiers.*] at any local or cloud provider.",
        "",
    ]
    _ATTR_OVERRIDES = {"workflow": "workflow_config"}
    for section_name, klass in _SECTION_MAP.items():
        section = getattr(cfg, _ATTR_OVERRIDES.get(section_name, section_name))
        if section_name == "intelligence":
            lines.append("[intelligence]")
            for f in fields(IntelligenceConfig):
                if f.name == "tiers":
                    continue
                lines.append(f"{f.name} = {_fmt_value(getattr(section, f.name))}")
            lines.append("")
            for tier_name, tier in section.tiers.items():
                lines.append(f"[tiers.{tier_name}]")
                for f in fields(TierConfig):
                    val = getattr(tier, f.name)
                    if f.name == "extra" and not val:
                        continue
                    lines.append(f"{f.name} = {_fmt_value(val)}")
                lines.append("")
            continue
        if section_name == "engine":
            lines.append("[engine]")
            for f in fields(EngineConfig):
                if f.name == "adapters":
                    continue
                lines.append(f"{f.name} = {_fmt_value(getattr(section, f.name))}")
            lines.append("")
            for ad_name, ad in section.adapters.items():
                lines.append(f"[engine.adapters.{ad_name}]")
                for k, v in ad.items():
                    lines.append(f"{k} = {_fmt_value(v)}")
                lines.append("")
            continue
        if section_name == "agent":
            lines.append("[agent]")
            for f in fields(AgentConfig):
                if f.name == "agents":
                    continue
                lines.append(f"{f.name} = {_fmt_value(getattr(section, f.name))}")
            lines.append("")
            for ag_name, ag in section.agents.items():
                lines.append(f"[agents.{ag_name}]")
                for k, v in ag.items():
                    lines.append(f"{k} = {_fmt_value(v)}")
                lines.append("")
            continue
        if section_name == "channels":
            lines.append("[channels]")
            lines.append(f"enabled = {_fmt_value(section.enabled)}")
            if section.default_agent:
                lines.append(f'default_agent = {_fmt_value(section.default_agent)}')
            lines.append(f"max_length = {_fmt_value(section.max_length)}")
            lines.append("")
            for ch_name, ch_options in (section.channels or {}).items():
                lines.append(f"[channels.{ch_name}]")
                for k, v in ch_options.items():
                    lines.append(f"{k} = {_fmt_value(v)}")
                lines.append("")
            continue
        if section_name == "tools":
            _dump_section("tools.storage", section.storage, lines)
            _dump_section("tools.mcp", section.mcp, lines)
            _dump_section("tools.browser", section.browser, lines)
            _dump_section("tools.weather", section.weather, lines)
            lines.append("[tools]")
            lines.append(f"enabled = {_fmt_value(section.enabled)}")
            lines.append(f"disabled = {_fmt_value(section.disabled)}")
            lines.append("")
            continue
        _dump_section(section_name, section, lines)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# Global accessor
# --------------------------------------------------------------------------- #
_lock = threading.RLock()
_active: CarlaConfig | None = None


def set_active_config(cfg: CarlaConfig) -> None:
    global _active
    with _lock:
        _active = cfg


def get_config() -> CarlaConfig:
    global _active
    with _lock:
        if _active is None:
            _active = load_config()
        return _active


def reload_config(path: str | Path | None = None) -> CarlaConfig:
    cfg = load_config(path)
    set_active_config(cfg)
    return cfg
