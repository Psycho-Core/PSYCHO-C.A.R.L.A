"""
PSYCHO-C.A.R.L.A. — Test suite.

Validates the five primitives, the persona/channel splitter, the tier router,
the memory store, the cron scheduler and the HTTP/WebSocket surface.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycho_carla.agents.builtin import BUILTIN_AGENTS
from psycho_carla.agents.scheduler import CronSchedule
from psycho_carla.core.config import config_from_dict, load_config
from psycho_carla.core.persona import Persona, condense_for_speech, looks_like_code
from psycho_carla.core.runtime import CarlaRuntime
from psycho_carla.core.telemetry import Telemetry
from psycho_carla.learning.optimizer import PromptOptimizer
from psycho_carla.learning.traces import TraceLogger
from psycho_carla.memory.store import Embedder, MemoryStore
from psycho_carla.providers.base import GenerationResult, Message, Usage
from psycho_carla.providers.router import ModelRouter
from psycho_carla.tools.registry import ToolRegistry


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture()
def tmp_home(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("PSYCHO_CARLA_HOME", td)
        yield Path(td)


@pytest.fixture()
def sample_config(tmp_home):
    return config_from_dict(
        {
            "intelligence": {
                "default_tier": "fast_tier",
                "temperature": 0.7,
                "max_tokens": 1024,
            },
            "tiers": {
                "fast_tier": {
                    "provider": "ollama",
                    "model": "test-fast",
                    "temperature": 0.5,
                    "max_tokens": 512,
                },
                "deep_tier": {
                    "provider": "openai_compat",
                    "model": "test-deep",
                    "api_key_env": "TEST_KEY",
                },
                "vision_tier": {
                    "provider": "ollama",
                    "model": "test-vision",
                },
            },
            "agent": {"default_agent": "orchestrator", "max_turns": 4},
            "voice": {"enabled": False},
            "memory": {"enabled": True},
            "tools": {"mcp": {"enabled": False}},
        },
        config_path=tmp_home / "psycho_carla.toml",
    )


@pytest.fixture()
def runtime(sample_config):
    rt = CarlaRuntime(sample_config, with_voice=False, with_mcp=False)
    yield rt
    rt.stop()


# --------------------------------------------------------------------------- #
# Pillar 1 — Intelligence / Model Router
# --------------------------------------------------------------------------- #
class TestModelRouter:
    def test_tier_alias_resolution(self, sample_config):
        router = ModelRouter(sample_config)
        res = router.resolve("deep_tier")
        assert res.tier == "deep_tier"
        assert res.provider == "openai_compat"
        assert res.model == "test-deep"

    def test_default_tier_used_when_unspecified(self, sample_config):
        router = ModelRouter(sample_config)
        assert router.resolve().tier == "fast_tier"

    def test_unknown_tier_falls_back(self, sample_config):
        router = ModelRouter(sample_config)
        assert router.resolve("nonexistent_tier").tier == "fast_tier"

    def test_per_tier_sampling_overrides(self, sample_config):
        router = ModelRouter(sample_config)
        assert router.resolve("fast_tier").sampling["temperature"] == 0.5
        assert router.resolve("fast_tier").sampling["max_tokens"] == 512

    def test_active_tier_switching(self, sample_config):
        router = ModelRouter(sample_config)
        assert router.set_active_tier("deep_tier") is True
        assert router.active_tier == "deep_tier"
        assert router.set_active_tier("bogus") is False

    def test_no_hardcoded_model_names(self, sample_config):
        """The router must never contain a literal model identifier."""
        import psycho_carla.providers.router as router_mod

        source = Path(router_mod.__file__).read_text(encoding="utf-8")
        for forbidden in ("llama3", "qwen", "mistral", "gpt-4", "phi-3", "gemini",
                          "llama2", "falcon", "codellama", "mixtral"):
            assert forbidden not in source.lower(), f"{forbidden} hardcoded in router"

    def test_runtime_layers_are_model_agnostic(self):
        """No model identifier may appear in the runtime, agents or adapters.

        Only the config template (a comment-laden example file) may name models.
        """
        import psycho_carla

        pkg_root = Path(psycho_carla.__file__).parent
        forbidden = ("llama3", "qwen2.5", "mistral", "gpt-4o", "gpt-4-turbo",
                     "phi-3", "gemini-1", "codellama", "mixtral", "llama2-")

        for rel in (
            "providers/router.py",
            "providers/base.py",
            "core/runtime.py",
            "core/config.py",
            "agents/base.py",
            "agents/builtin/__init__.py",
        ):
            source = (pkg_root / rel).read_text(encoding="utf-8").lower()
            for name in forbidden:
                assert name not in source, f"{name} hardcoded in {rel}"

    def test_adapters_do_not_name_models(self):
        """Adapters take the model from config, never from source."""
        import psycho_carla

        pkg_root = Path(psycho_carla.__file__).parent
        forbidden = ("llama3", "qwen2.5", "mistral", "gpt-4o", "phi-3", "gemini-1")
        for path in (pkg_root / "providers" / "adapters").glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for name in forbidden:
                assert name not in source, f"{name} hardcoded in {path.name}"

    def test_tier_listing(self, sample_config):
        router = ModelRouter(sample_config)
        tiers = router.list_tiers()
        assert len(tiers) == 3
        assert {t["alias"] for t in tiers} == {"fast_tier", "deep_tier", "vision_tier"}


class TestAdapters:
    def test_all_adapters_registered(self):
        from psycho_carla.providers.base import available_adapters, load_builtin_adapters

        load_builtin_adapters()
        names = available_adapters()
        for expected in ("ollama", "openai_compat", "llamacpp", "generic_http"):
            assert expected in names

    def test_adapter_canonical_interface(self):
        from psycho_carla.providers.base import ModelAdapter

        assert hasattr(ModelAdapter, "generate")
        sig_names = ModelAdapter.generate.__code__.co_varnames
        assert "messages" in sig_names
        assert "capability_tier" in sig_names
        assert "stream" in sig_names

    def test_unknown_adapter_raises(self):
        from psycho_carla.providers.base import build_adapter

        with pytest.raises(KeyError):
            build_adapter("does_not_exist")


# --------------------------------------------------------------------------- #
# Pillar 2 — Engine / API
# --------------------------------------------------------------------------- #
class TestServer:
    def test_app_creates(self, sample_config):
        from fastapi.testclient import TestClient
        from psycho_carla.server.app import create_app

        app = create_app(sample_config, runtime=None, start_runtime=False)
        with TestClient(app) as client:
            res = client.get("/api/status")
            assert res.status_code == 200
            body = res.json()
            assert "tiers" in body
            assert "agents" in body

    def test_openai_compat_endpoint(self, sample_config):
        from fastapi.testclient import TestClient
        from psycho_carla.server.app import create_app

        app = create_app(sample_config, start_runtime=False)
        with TestClient(app) as client:
            res = client.get("/v1/models")
            assert res.status_code == 200
            ids = [m["id"] for m in res.json()["data"]]
            assert "fast_tier" in ids
            assert "deep_tier" in ids

    def test_config_editor_roundtrip(self, sample_config):
        from fastapi.testclient import TestClient
        from psycho_carla.server.app import create_app

        app = create_app(sample_config, start_runtime=False)
        with TestClient(app) as client:
            toml = "[intelligence]\ndefault_tier = \"deep_tier\"\n"
            res = client.put("/api/config", json={"toml": toml})
            assert res.status_code == 200
            res = client.get("/api/config")
            assert "deep_tier" in res.json()["toml"]

    def test_invalid_toml_rejected(self, sample_config):
        from fastapi.testclient import TestClient
        from psycho_carla.server.app import create_app

        app = create_app(sample_config, start_runtime=False)
        with TestClient(app) as client:
            res = client.put("/api/config", json={"toml": "this is [not valid toml"})
            assert res.status_code == 400


# --------------------------------------------------------------------------- #
# Pillar 3 — Agents
# --------------------------------------------------------------------------- #
class TestAgents:
    def test_eight_builtin_agents(self):
        names = {c.name for c in BUILTIN_AGENTS}
        assert {
            "orchestrator", "operative", "deep_research", "morning_digest",
            "code_assist", "monitor_operative", "memory_curator", "scheduler",
        } <= names
        # The extended upstream registrations are present too.
        assert {
            "simple", "native_react", "native_openhands", "rlm", "proactive",
            "conductor", "minions", "advisors", "skillorchestra",
            "toolorchestra",
        } <= names

    def test_execution_modes_covered(self):
        modes = {c.mode for c in BUILTIN_AGENTS}
        assert modes == {"on_demand", "scheduled", "continuous"}

    def test_agents_registered_in_runtime(self, runtime):
        listed = {a["name"] for a in runtime.list_agents()}
        assert len(listed) == len(BUILTIN_AGENTS)
        assert len(listed) >= 26

    def test_get_agent_returns_instance(self, runtime):
        agent = runtime.get_agent("orchestrator")
        assert agent is not None
        assert agent.name == "orchestrator"
        assert agent.max_turns >= 1

    def test_unknown_agent_returns_none(self, runtime):
        assert runtime.get_agent("not_an_agent") is None


class TestScheduler:
    def test_cron_parsing(self):
        cron = CronSchedule.parse("*/15 * * * *")
        assert 0 in cron.minutes and 15 in cron.minutes and 30 in cron.minutes

    def test_cron_specific_time(self):
        cron = CronSchedule.parse("30 7 * * 1-5")
        assert cron.hours == {7}
        assert cron.minutes == {30}
        assert cron.weekdays == {1, 2, 3, 4, 5}

    def test_invalid_cron_raises(self):
        with pytest.raises(ValueError):
            CronSchedule.parse("not a cron")

    def test_job_lifecycle(self, runtime):
        job = runtime.add_job("test", "0 9 * * *", agent="morning_digest", prompt="hi")
        assert job.id
        assert len(runtime.list_jobs()) == 1
        assert runtime.remove_job(job.id) is True
        assert runtime.list_jobs() == []


# --------------------------------------------------------------------------- #
# Pillar 4 — Tools & Memory
# --------------------------------------------------------------------------- #
class TestTools:
    def test_builtin_tools_registered(self, runtime):
        names = set(runtime.tools.names())
        for expected in ("file_read", "file_write", "shell_exec", "web_search",
                         "calculator", "code_execute"):
            assert expected in names

    def test_calculator_exact(self):
        from psycho_carla.tools.builtin.code import calculator

        assert calculator("2 + 2 * 10") == "22"
        assert calculator("(3+4)*2/7") == "2"
        assert "Error" in calculator("__import__('os')")

    def test_file_tools_sandboxed(self, runtime):
        from psycho_carla.tools.builtin.files import file_read

        out = file_read("/etc/shadow")
        assert "Error" in out or "outside" in out

    def test_tool_execution_records_history(self, runtime):
        runtime.tools.execute("calculator", {"expression": "1+1"})
        assert runtime.tools.history()

    def test_unknown_tool_returns_error(self, runtime):
        result = runtime.tools.execute("nope", {})
        assert result.ok is False
        assert "Unknown tool" in result.error

    def test_tool_schema_generation(self, runtime):
        schemas = runtime.tools.schemas()
        assert all("function" in s for s in schemas)
        names = {s["function"]["name"] for s in schemas}
        assert "calculator" in names


class TestMemory:
    def test_store_and_search(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=128))
        store.store("I prefer Python over JavaScript", kind="preference")
        store.store("The project uses PostgreSQL on port 5432", kind="fact")
        store.store("Bicycle repair shop on the corner", kind="note")

        hits = store.search("python", top_k=3)
        assert hits
        assert "Python" in hits[0].content

    def test_empty_store_search(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=64))
        assert store.search("anything") == []

    def test_conversation_history(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=64))
        store.record_message("s1", "user", "hello")
        store.record_message("s1", "assistant", "hi there")
        conv = store.get_conversation("s1")
        assert len(conv) == 2
        assert conv[0].role == "user"

    def test_delete_and_stats(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=64))
        mid = store.store("temporary note")
        assert store.delete(mid) is True
        assert store.stats()["memories"] == 0

    def test_duplicate_memories_are_deduped(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=64))
        a = store.store("Boss prefers dark mode", tags=["ui"])
        b = store.store("Boss prefers dark mode", tags=["preference"])
        assert a == b
        assert store.stats()["memories"] == 1
        # Tags from both writes are merged.
        tags = store.get(a).tags
        assert "ui" in tags and "preference" in tags

    def test_dedupe_can_be_bypassed(self, tmp_home):
        store = MemoryStore(tmp_home / "m.db", Embedder(dim=64))
        a = store.store("same text", dedupe=True)
        b = store.store("same text", dedupe=False)
        assert a != b


class TestMCP:
    def test_manager_configures_servers(self):
        from psycho_carla.tools.mcp.client import MCPManager

        mgr = MCPManager()
        mgr.configure(
            {"demo": {"transport": "stdio", "command": "echo"}},
            enabled=True,
        )
        assert "demo" in mgr.servers
        assert mgr.servers["demo"].command == "echo"

    def test_mcp_server_registers_tools(self):
        from psycho_carla.tools.mcp.server import build_default_server

        server = build_default_server()
        assert "carla_ask" in server.tools
        assert "carla_memory_search" in server.tools


# --------------------------------------------------------------------------- #
# Pillar 5 — Learning
# --------------------------------------------------------------------------- #
class TestLearning:
    def test_trace_logging(self, tmp_home):
        logger = TraceLogger(tmp_home / "t.db")
        tid = logger.log_exchange(
            prompt="hello", response="hi", agent="orchestrator",
            tier="fast_tier", latency_ms=120.0,
            usage={"total_tokens": 42},
        )
        assert tid
        assert logger.stats()["total"] == 1

    def test_optimizer_produces_suggestion(self, tmp_home):
        logger = TraceLogger(tmp_home / "t.db")
        for i in range(10):
            logger.log_exchange(
                prompt=f"question {i}",
                response="Here is what I found. The answer is 42, because it is.",
                agent="orchestrator", tier="fast_tier", latency_ms=200.0,
            )
            logger.rate(i + 1, 5.0)
        opt = PromptOptimizer(logger)
        suggestion = opt.optimize_prompt("orchestrator", min_samples=3)
        assert suggestion is not None
        assert suggestion.proposed_prompt
        assert suggestion.sample_count >= 3

    def test_routing_policy_learns(self, tmp_home):
        logger = TraceLogger(tmp_home / "t.db")
        for i in range(6):
            logger.log_exchange(
                prompt="q", response="Step 1: do the thing. The answer is clear.",
                agent="code_assist", tier="deep_tier", latency_ms=300.0,
            )
        opt = PromptOptimizer(logger)
        policies = opt.optimize_routing(min_samples=3)
        assert "code_assist" in policies
        assert policies["code_assist"].recommended_tier == "deep_tier"


# --------------------------------------------------------------------------- #
# Persona & dual-channel splitting
# --------------------------------------------------------------------------- #
class TestPersona:
    def test_code_is_routed_to_ui_not_speech(self):
        persona = Persona()
        response = (
            "Here is the fix:\n\n"
            "```python\ndef hello():\n    print('hi')\n```\n\n"
            "That adds a greeting function."
        )
        channel = persona.split(response)
        assert "def hello" not in channel.speech
        assert "def hello" in channel.display
        assert len(channel.code_blocks) == 1

    def test_prose_is_summarised_for_speech(self):
        persona = Persona()
        response = (
            "The build failed on line 42 because the dependency was not installed. "
            "I installed it and the build now passes cleanly."
        )
        channel = persona.split(response)
        assert channel.speech
        assert len(channel.speech) < len(response)
        assert "Boss" in channel.speech or channel.speech[0].isupper()

    def test_banned_filler_removed(self):
        persona = Persona()
        response = (
            "Certainly! I am an AI, but I will help. "
            "As always, the server is on port 8080."
        )
        channel = persona.split(response)
        assert "as an ai" not in channel.speech.lower()
        assert "certainly" not in channel.speech.lower()
        assert "as always" not in channel.speech.lower()

    def test_diff_not_spoken(self):
        persona = Persona()
        diff = (
            "Here is the patch:\n\n"
            "```diff\n--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,4 @@\n+import os\n```\n\n"
            "That imports os."
        )
        channel = persona.split(diff)
        assert "+import os" not in channel.speech
        assert "+import os" in channel.display

    def test_code_only_response(self):
        persona = Persona()
        response = "```python\nprint('x')\n```"
        channel = persona.split(response)
        assert channel.is_code_only
        assert channel.speech
        assert "print" not in channel.speech

    def test_boss_addressing(self):
        persona = Persona()
        assert "Psycho" in persona.salutation("greeting")

    def test_looks_like_code_detection(self):
        assert looks_like_code("def f():\n    return 1\n\ndef g():\n    return 2")
        assert not looks_like_code(
            "The server is running on port 8080 and everything looks healthy."
        )

    def test_condense_respects_limit(self):
        long_text = "word " * 400
        out = condense_for_speech(long_text, max_chars=100)
        assert len(out) <= 105

    def test_bullets_flattened(self):
        out = condense_for_speech("First item here.\n- Second item.\n- Third item.")
        assert "," in out or "and" in out
        assert "- Second" not in out


# --------------------------------------------------------------------------- #
# Telemetry
# --------------------------------------------------------------------------- #
class TestTelemetry:
    def test_request_metrics_recorded(self, tmp_home):
        import time

        tel = Telemetry(tmp_home / "tel.db")
        from psycho_carla.core.telemetry import RequestMetric

        tel.record_request(
            RequestMetric(
                request_id="r1", agent="orchestrator", tier="fast_tier",
                provider="ollama", model="m", started_at=time.time(),
                latency_ms=123.0, completion_tokens=100, total_tokens=150,
                tokens_per_second=45.0,
            )
        )
        summary = tel.summary()
        assert summary["requests"] == 1
        assert summary["avg_latency_ms"] == 123.0
        assert summary["avg_tokens_per_second"] == 45.0
        tel.close()

    def test_resource_sampling(self, tmp_home):
        tel = Telemetry(tmp_home / "tel.db")
        sample = tel.sample_now()
        assert sample.ram_total_mb > 0
        tel.close()


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
class TestConfig:
    def test_tiers_parsed(self, sample_config):
        assert len(sample_config.intelligence.tiers) == 3
        assert sample_config.intelligence.tiers["fast_tier"].model == "test-fast"

    def test_legacy_default_model_migration(self, tmp_home):
        cfg = config_from_dict(
            {
                "intelligence": {"default_model": "legacy-model", "default_tier": "fast_tier"},
                "tiers": {"fast_tier": {"provider": "ollama"}},
            }
        )
        assert cfg.intelligence.tiers["fast_tier"].model == "legacy-model"

    def test_legacy_context_injection_migration(self, tmp_home):
        cfg = config_from_dict({"memory": {"context_injection": False}})
        assert cfg.agent.context_from_memory is False

    def test_config_roundtrip(self, tmp_home):
        from psycho_carla.core.config import dump_toml, save_config

        cfg = config_from_dict(
            {"tiers": {"fast_tier": {"provider": "ollama", "model": "m"}}},
            config_path=tmp_home / "c.toml",
        )
        text = dump_toml(cfg)
        assert "[tiers.fast_tier]" in text
        reparsed = config_from_dict(
            __import__("tomllib").loads(text), config_path=tmp_home / "c.toml"
        )
        assert reparsed.intelligence.tiers["fast_tier"].model == "m"

    def test_default_template_is_valid_toml(self):
        import tomllib

        from psycho_carla.config_template import render_default_config

        parsed = tomllib.loads(render_default_config())
        assert "tiers" in parsed
        assert "intelligence" in parsed
        assert "voice" in parsed
        assert "telemetry" in parsed


# --------------------------------------------------------------------------- #
# Runtime end-to-end
# --------------------------------------------------------------------------- #
class TestRuntime:
    def test_runtime_starts_and_stops(self, runtime):
        runtime.start()
        assert runtime.status()["started"] is True
        runtime.stop()

    def test_ask_with_fake_provider(self, sample_config):
        """Patch the adapter so no real model is required."""
        rt = CarlaRuntime(sample_config, with_voice=False, with_mcp=False)

        class FakeAdapter:
            name = "ollama"

            def generate(self, messages, capability_tier="fast_tier", stream=False, **kw):
                return GenerationResult(
                    text="All good, Boss. The server is healthy on port 8080.",
                    usage=Usage(prompt_tokens=10, completion_tokens=20),
                    model="fake", provider="ollama", tier=capability_tier,
                )

            def health(self):
                return True, "ok"

            def list_models(self):
                return []

        rt.router.get_adapter = lambda provider, tier: FakeAdapter()  # type: ignore
        result = rt.ask("How are things?")
        assert result["text"]
        assert result["speech"]
        assert result["agent"] == "orchestrator"
        assert result["usage"]["total_tokens"] == 30
        rt.stop()

    def test_unknown_agent_error_handled(self, runtime):
        result = runtime.ask("hello", agent="nonexistent")
        assert "error" in result
        assert "nonexistent" in result["error"]

    def test_status_shape(self, runtime):
        status = runtime.status()
        for key in ("tiers", "agents", "tools", "memory", "telemetry", "hardware"):
            assert key in status


# --------------------------------------------------------------------------- #
# Security
# --------------------------------------------------------------------------- #
class TestSecurity:
    def test_shell_can_be_disabled(self, sample_config):
        from psycho_carla.tools.builtin import shell as shell_tools

        shell_tools.configure_shell(allow=False)
        out = shell_tools.shell_exec("echo hi")
        assert "disabled" in out.lower()
        shell_tools.configure_shell(allow=True)

    def test_file_sandbox_blocks_escape(self):
        from psycho_carla.tools.builtin.files import FileSandbox

        sandbox = FileSandbox(root=tempfile.gettempdir())
        with pytest.raises(PermissionError):
            sandbox.resolve("C:/Windows/System32/config/SAM")

    def test_shell_allowlist(self):
        from psycho_carla.tools.builtin import shell as shell_tools

        shell_tools.configure_shell(allow=True, allowed_commands=["echo"])
        out = shell_tools.shell_exec("rm -rf /")
        assert "not in the allowed list" in out
        shell_tools.configure_shell(allow=True, allowed_commands=[])


class TestVoiceBackends:
    """Regression tests for the CPU-only voice pipeline."""

    def test_voice_aliases_map_to_real_voice_ids(self):
        """Every configured alias must resolve to a real voice id.

        Neither Piper nor Kokoro ships an Irish (en_IE) voice, so the
        `irish_*` aliases must point at the closest British voices.
        """
        from psycho_carla.voice.tts import KOKORO_VOICE_MAP, PIPER_VOICE_MAP

        assert KOKORO_VOICE_MAP["irish_female"] == "bf_emma"
        assert KOKORO_VOICE_MAP["irish_male"] == "bm_george"
        assert PIPER_VOICE_MAP["irish_female"].startswith("en_GB")
        assert PIPER_VOICE_MAP["irish_male"].startswith("en_GB")

    def test_kokoro_lang_code_matches_voice_prefix(self):
        """The G2P language pack must follow the voice id prefix."""
        from psycho_carla.voice.tts import KokoroTTS

        assert KokoroTTS(voice="bf_emma")._lang_code() == "b"
        assert KokoroTTS(voice="bm_george")._lang_code() == "b"
        assert KokoroTTS(voice="af_heart")._lang_code() == "a"
        assert KokoroTTS(voice="am_michael")._lang_code() == "a"

    def test_configure_espeak_never_reexecs(self):
        """Library code must never re-exec the process.

        A re-exec from inside VoiceSystem would restart the test suite
        or the running server as a side effect.
        """
        import os as _os

        from psycho_carla.voice.tts import configure_espeak_environment

        before = _os.getpid()
        configure_espeak_environment()
        assert _os.getpid() == before, "configure_espeak_environment re-exec'd"

    def test_reexec_helper_is_opt_in(self):
        """The re-exec helper exists but is not called from library code."""
        import inspect

        from psycho_carla.voice import tts

        src = inspect.getsource(tts)
        # It must be defined, and it must be the only thing doing execve.
        assert "maybe_reexec_with_espeak" in src
        assert src.count("os.execve") == 1
        # configure_espeak_environment must not call it.
        fn = inspect.getsource(tts.configure_espeak_environment)
        assert "execve" not in fn
        assert "maybe_reexec" not in fn

    def test_silent_backend_is_the_fallback(self):
        """A missing TTS backend must degrade to silent, never raise."""
        from psycho_carla.voice.tts import build_tts

        engine = build_tts(engine="does-not-exist", voice="irish_female")
        assert engine.name == "silent"
        result = engine.synthesize("hello", "/tmp/_carla_should_not_exist.wav")
        assert result.ok

    def test_voice_config_exposes_model_path(self):
        """Config must allow pointing at a local Kokoro checkpoint."""
        from psycho_carla.core.config import VoiceConfig

        vc = VoiceConfig()
        assert hasattr(vc, "tts_model_path")


# --------------------------------------------------------------------------- #
# Parity closure: energy telemetry, learning surfaces, auth, bench/eval,
# and the skills catalog.
# --------------------------------------------------------------------------- #
class TestEnergyTelemetry:
    """Energy + FLOPs are first-class constraints, not afterthoughts."""

    def test_config_exposes_energy_fields(self):
        from psycho_carla.core.config import TelemetryConfig

        tc = TelemetryConfig()
        assert hasattr(tc, "energy_vendor")
        assert hasattr(tc, "track_energy")
        assert hasattr(tc, "gpu_metrics")
        assert hasattr(tc, "gpu_poll_interval_ms")
        assert hasattr(tc, "warmup_samples")
        assert hasattr(tc, "steady_state_window")
        assert hasattr(tc, "steady_state_threshold")

    def test_flops_estimation_uses_two_n_rule(self):
        from psycho_carla.core.energy import estimate_flops

        # 2 * 7e9 params * 150 tokens
        flops = estimate_flops(100, 50, 7.0)
        assert flops == 2 * 7e9 * 150
        assert estimate_flops(0, 0, 7.0) == 0.0
        # Unknown parameter count must be unknown, not zero.
        assert estimate_flops(100, 50, 0.0) is None
        assert estimate_flops(100, 50, None) is None

    def test_param_count_from_model_name(self):
        from psycho_carla.core.energy import params_from_model_name

        assert params_from_model_name("qwen2.5-14b-instruct") == 14.0
        assert params_from_model_name("llama3.2-vision:11b") == 11.0
        assert params_from_model_name("mystery-model") is None

    def test_energy_monitor_never_raises(self):
        from psycho_carla.core.energy import EnergyMonitor

        monitor = EnergyMonitor(vendor="auto")
        sample = monitor.sample()
        assert hasattr(sample, "watts")
        assert hasattr(sample, "vendor")
        assert sample.watts >= 0.0

    def test_unknown_vendor_is_honest(self):
        from psycho_carla.core.energy import EnergyMonitor

        monitor = EnergyMonitor(vendor="none")
        assert monitor.resolve_vendor() is None
        sample = monitor.sample()
        assert sample.available is False

    def test_telemetry_records_energy_and_flops(self):
        import tempfile, os
        from psycho_carla.core.telemetry import Telemetry, RequestMetric

        with tempfile.TemporaryDirectory() as d:
            tel = Telemetry(os.path.join(d, "t.db"), track_energy=True)
            tel.sample_now()
            metric = RequestMetric(
                request_id="r1", agent="a", tier="fast_tier",
                provider="p", model="qwen2.5-7b-instruct", started_at=time.time(),
            )
            metric.prompt_tokens = 100
            metric.completion_tokens = 50
            metric.total_tokens = 150
            metric.flops = 2 * 7e9 * 150
            metric.energy_joules = 12.5
            metric.power_watts = 250.0
            tel.record_request(metric)

            summary = tel.summary()
            assert summary["total_flops"] == 2 * 7e9 * 150
            assert summary["energy"]["total_joules"] == 12.5
            energy = tel.energy_summary()
            assert energy["models"][0]["model"] == "qwen2.5-7b-instruct"
            assert energy["models"][0]["total_flops"] == 2 * 7e9 * 150

    def test_resources_schema_has_power_columns(self):
        import tempfile, os, sqlite3
        from psycho_carla.core.telemetry import Telemetry

        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "t.db")
            tel = Telemetry(db, track_energy=True)
            tel.sample_now()
            tel.stop()
            conn = sqlite3.connect(db)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(resources)")}
            conn.close()
            assert "power_watts" in cols
            assert "energy_vendor" in cols


class TestLearningSurfaces:
    """Learning optimises four surfaces, not two."""

    def test_config_exposes_learning_surfaces(self):
        from psycho_carla.core.config import LearningConfig

        lc = LearningConfig()
        assert hasattr(lc, "tool_selection")
        assert hasattr(lc, "engine")
        assert hasattr(lc, "weights")
        assert lc.weights["accuracy"] == 0.6
        assert lc.weights["latency"] == 0.2

    def test_policy_switches_gate_surfaces(self):
        from psycho_carla.core.config import LearningConfig

        lc = LearningConfig()
        lc.routing = {"policy": "heuristic"}
        lc.tools = {"policy": ""}
        lc.engine = {"policy": "engine_spec"}
        assert lc.policy_for("routing") == "heuristic"
        assert lc.policy_for("tools") == ""
        assert lc.surface_enabled("tools") is False
        assert lc.surface_enabled("engine") is True

    def test_policy_none_disables_surface(self):
        from psycho_carla.core.config import LearningConfig

        lc = LearningConfig()
        lc.agent = {"policy": "none"}
        assert lc.policy_for("agent") == ""

    def _traces(self, tmp):
        from psycho_carla.learning.traces import TraceLogger

        logger = TraceLogger(os.path.join(tmp, "tr.db"), enabled=True)
        ids = []
        for _ in range(8):
            ids.append((logger.log_exchange(
                agent="coder", tier="fast_tier", provider="p",
                model="qwen2.5-7b-instruct", prompt="q", response="a" * 120,
                latency_ms=800, tool_calls=[{"name": "web_search"}],
            ), 5))
        for _ in range(6):
            ids.append((logger.log_exchange(
                agent="coder", tier="fast_tier", provider="p",
                model="qwen2.5-7b-instruct", prompt="q", response="short",
                latency_ms=3000, tool_calls=[{"name": "calculator"}],
                metadata={"error": "boom"},
            ), 1))
        logger.flush()
        for tid, rating in ids:
            if tid:
                logger.rate(tid, rating)
        logger.flush()
        return logger

    def test_tool_selection_optimizer_keeps_good_drops_bad(self):
        from psycho_carla.learning.surfaces import ToolSelectionOptimizer

        with tempfile.TemporaryDirectory() as d:
            logger = self._traces(d)
            policies = ToolSelectionOptimizer(logger).optimize_tool_selection(
                agent="coder"
            )
            policy = policies["coder"]
            assert "web_search" in policy.keep
            assert "web_search" in policy.promote
            assert "calculator" in policy.drop
            assert policy.scores["web_search"] > policy.scores["calculator"]

    def test_engine_spec_optimizer_proposes_config(self):
        from psycho_carla.learning.surfaces import EngineSpecOptimizer

        with tempfile.TemporaryDirectory() as d:
            logger = self._traces(d)
            specs = EngineSpecOptimizer(logger).optimize_engine_spec()
            assert "fast_tier" in specs
            spec = specs["fast_tier"]
            assert spec.model == "qwen2.5-7b-instruct"
            assert spec.quantization in (
                "none", "q8_0", "q5_k_m", "q4_k_m", "q3_k_s"
            )
            assert spec.context_window >= 4096
            assert spec.temperature == 0.3  # fast tier favours determinism

    def test_skills_optimizer_flags_vague_description(self):
        from psycho_carla.learning.skills_optimizer import SkillOptimizer
        from psycho_carla.skills.catalog import Skill

        vague = Skill(name="vague", description="Helps with various tasks.")
        report = SkillOptimizer().optimize(policy="dspy")
        assert report.scanned == 0  # no catalog attached

        class _Cat:
            def __iter__(self):
                return iter([vague])

        report = SkillOptimizer(_Cat()).optimize(policy="dspy")
        assert report.scanned == 1
        assert report.scores["vague"] < 0.8
        assert any("trigger" in i.message.lower() for i in report.issues)


class TestServerAuth:
    """A non-loopback bind must refuse to start without a key."""

    def test_loopback_detection(self):
        from psycho_carla.server.auth import is_loopback

        assert is_loopback("127.0.0.1")
        assert is_loopback("localhost")
        assert is_loopback("::1")
        assert not is_loopback("0.0.0.0")
        assert not is_loopback("192.168.1.10")

    def test_exposed_bind_without_key_is_refused(self):
        from psycho_carla.server.auth import resolve_auth

        with pytest.raises(RuntimeError) as exc:
            resolve_auth("0.0.0.0", 8787)
        assert "non-loopback" in str(exc.value)

    def test_loopback_bind_needs_no_key(self):
        from psycho_carla.server.auth import resolve_auth

        cfg = resolve_auth("127.0.0.1", 8787)
        assert cfg.exposed is False
        assert cfg.enabled is False

    def test_exposed_bind_with_key_is_allowed(self):
        from psycho_carla.server.auth import resolve_auth

        cfg = resolve_auth("0.0.0.0", 8787, api_key="s3cret")
        assert cfg.exposed is True
        assert cfg.enabled is True

    def test_key_extraction_and_comparison(self):
        from psycho_carla.server.auth import check_key, extract_key

        assert extract_key(authorization="Bearer abc123") == "abc123"
        assert extract_key(x_api_key="xyz") == "xyz"
        assert check_key("abc", "abc") is True
        assert check_key("abc", "xyz") is False
        assert check_key("", "xyz") is False
        # No expected key means no check.
        assert check_key("", "") is True

    def test_generate_key_is_unique(self):
        from psycho_carla.server.auth import generate_api_key

        a = generate_api_key()
        b = generate_api_key()
        assert a != b
        assert len(a) >= 32

    def test_server_config_exposes_auth_fields(self):
        from psycho_carla.core.config import ServerConfig

        sc = ServerConfig()
        assert hasattr(sc, "api_key_env")
        assert hasattr(sc, "api_key")
        assert sc.require_auth is True


class TestBenchEval:
    """Bench measures performance; eval measures quality."""

    def test_bench_workload_builders(self):
        from psycho_carla.bench import BenchWorkload

        chat = BenchWorkload.chat(turns=3)
        assert chat.name == "chat"
        assert len(chat.cases) == 3
        tools = BenchWorkload.tool_use(turns=4)
        assert len(tools.cases) == 4

    def test_bench_report_summary_shape(self):
        from psycho_carla.bench import BenchReport, BenchResult

        report = BenchReport(workload="chat")
        report.results.append(BenchResult(
            name="c1", agent="a", tier="t", ok=True,
            latency_ms=100.0, total_tokens=50, tokens_per_second=500.0,
            energy_joules=2.0, flops=1e12,
        ))
        summary = report.summary()
        assert summary["cases"] == 1
        assert summary["passed"] == 1
        assert summary["success_rate"] == 100.0
        assert summary["latency_ms"]["mean"] == 100.0
        assert summary["energy"]["total_joules"] == 2.0
        assert summary["total_flops"] == 1e12
        assert summary["flops_per_token"] == 1e12 / 50

    def test_bench_workload_roundtrips_json(self):
        from psycho_carla.bench import BenchWorkload

        original = BenchWorkload.chat(turns=2)
        restored = BenchWorkload.from_dict(original.to_dict())
        assert restored.name == original.name
        assert len(restored.cases) == 2

    def test_eval_suite_from_json(self):
        from psycho_carla.bench import EvalSuite

        payload = {
            "name": "demo",
            "cases": [{
                "name": "c1",
                "prompt": "hello",
                "expect_contains": ["hi"],
                "expect_regex": [r"\bhi\b"],
                "weight": 2.0,
            }],
        }
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "eval.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            suite = EvalSuite.from_json(path)
            assert suite.name == "demo"
            assert len(suite.cases) == 1
            assert suite.cases[0].weight == 2.0
            assert suite.cases[0].expect_contains == ["hi"]

    def test_eval_grades_contains_and_regex(self):
        from psycho_carla.bench import EvalCase, EvalSuite

        class _FakeRuntime:
            """Minimal runtime stub for grading."""

            telemetry = None

            def ask(self, prompt, *, agent="", tier="", **kwargs):
                return {
                    "text": "The answer is 4, obviously.",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 5},
                    "agent": agent,
                }

        suite = EvalSuite("demo", [
            EvalCase(name="contains", prompt="x", expect_contains=["4"]),
            EvalCase(name="regex", prompt="x", expect_regex=[r"\b4\b"]),
            EvalCase(name="not_contains", prompt="x",
                     expect_not_contains=["banana"]),
            EvalCase(name="fails", prompt="x", expect_contains=["zzz"]),
        ])
        report = suite.run(_FakeRuntime())
        results = {r.name: r for r in report.results}
        assert results["contains"].passed
        assert results["regex"].passed
        assert results["not_contains"].passed
        assert not results["fails"].passed
        assert report.summary()["accuracy"] == 75.0


class TestSkillsCatalog:
    """The agentskills.io specification, implemented locally."""

    def test_parses_valid_skill(self):
        from psycho_carla.skills import parse_skill

        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "pdf-processing")
            os.makedirs(folder)
            with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "---\n"
                    "name: pdf-processing\n"
                    "description: Extract PDF text and merge documents. "
                    "Use when working with PDF files.\n"
                    "license: Apache-2.0\n"
                    "metadata:\n"
                    "  author: example-org\n"
                    "  version: '1.0'\n"
                    "allowed-tools: Bash(git:*) Read\n"
                    "---\n\n"
                    "# PDF Processing\n\nDo the thing.\n"
                )
            skill = parse_skill(os.path.join(folder, "SKILL.md"))
            assert skill.name == "pdf-processing"
            assert skill.description.startswith("Extract PDF text")
            assert skill.license == "Apache-2.0"
            assert skill.author == "example-org"
            assert skill.version == "1.0"
            assert skill.allowed_tools == ["Bash(git:*)", "Read"]
            assert skill.errors == []
            assert skill.valid

    def test_rejects_invalid_name(self):
        from psycho_carla.skills import parse_skill

        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "Bad_Name")
            os.makedirs(folder)
            with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: Bad_Name\ndescription: x\n---\nbody\n")
            skill = parse_skill(os.path.join(folder, "SKILL.md"))
            assert not skill.valid
            assert any("lowercase" in e for e in skill.errors)

    def test_rejects_folder_name_mismatch(self):
        from psycho_carla.skills import parse_skill

        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "other-folder")
            os.makedirs(folder)
            with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: some-skill\ndescription: x\n---\nbody\n")
            skill = parse_skill(os.path.join(folder, "SKILL.md"))
            assert any("does not match directory" in e for e in skill.errors)

    def test_rejects_angle_brackets(self):
        from psycho_carla.skills import parse_skill

        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "inject-skill")
            os.makedirs(folder)
            with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "---\nname: inject-skill\n"
                    "description: Does a thing <script>alert(1)</script>\n---\nbody\n"
                )
            skill = parse_skill(os.path.join(folder, "SKILL.md"))
            assert any("angle brackets" in e for e in skill.errors)

    def test_rejects_missing_description(self):
        from psycho_carla.skills import parse_skill

        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "no-desc")
            os.makedirs(folder)
            with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: no-desc\n---\nbody\n")
            skill = parse_skill(os.path.join(folder, "SKILL.md"))
            assert any("description" in e for e in skill.errors)

    def test_store_install_list_uninstall(self):
        from psycho_carla.skills import SkillStore

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src", "my-skill")
            os.makedirs(src)
            with open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write(
                    "---\nname: my-skill\n"
                    "description: Does the thing. Use when you need the thing.\n"
                    "---\n# My Skill\n"
                )
            root = os.path.join(d, "catalog")
            store = SkillStore(root)
            assert len(store.catalog) == 0

            skill = store.install(src)
            assert skill.name == "my-skill"
            assert store.catalog.names == ["my-skill"]

            # Installing again without overwrite must fail.
            with pytest.raises(FileExistsError):
                store.install(src)

            assert store.uninstall("my-skill") is True
            assert len(store.catalog) == 0

    def test_store_rejects_noncompliant_skill(self):
        from psycho_carla.skills import SkillStore

        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "Bad_Name")
            os.makedirs(src)
            with open(os.path.join(src, "SKILL.md"), "w", encoding="utf-8") as fh:
                fh.write("---\nname: Bad_Name\ndescription: x\n---\nbody\n")
            store = SkillStore(os.path.join(d, "catalog"))
            with pytest.raises(ValueError):
                store.install(src)

    def test_catalog_search_ranks_by_trigger(self):
        from psycho_carla.skills.catalog import Skill, SkillCatalog

        report = Skill(
            name="status-report",
            description="Produce a status report. Use when asked for a "
                        "weekly summary or briefing.",
        )
        other = Skill(
            name="pdf-processing",
            description="Extract PDF text. Use when working with PDF files.",
        )
        catalog = SkillCatalog([report, other])
        matches = catalog.search("weekly status briefing", limit=5)
        assert matches
        assert matches[0].skill.name == "status-report"

    def test_metadata_prompt_contains_only_name_and_description(self):
        from psycho_carla.skills.catalog import Skill, SkillCatalog

        skill = Skill(
            name="my-skill",
            description="Does the thing.",
            body="A very long body that should not appear in the metadata prompt.",
        )
        prompt = SkillCatalog([skill]).metadata_prompt()
        assert "my-skill" in prompt
        assert "Does the thing." in prompt
        assert "very long body" not in prompt

    def test_skills_config_fields(self):
        from psycho_carla.core.config import SkillsConfig

        sc = SkillsConfig()
        assert sc.enabled is True
        assert sc.auto_load is True
        assert hasattr(sc, "max_skills")
        assert hasattr(sc, "validate_on_load")


def test_ask_passes_history_to_the_agent(runtime):
    """Regression: history given to Runtime.ask was silently dropped."""
    from psycho_carla.agents.base import BaseAgent
    from psycho_carla.providers.base import Message

    seen = {}
    orig = BaseAgent.run

    def spy(self, prompt, **kw):
        seen.update(kw)
        return orig(self, prompt, **kw)

    BaseAgent.run = spy
    try:
        runtime.ask("hi", history=[Message(role="user", content="earlier")])
    finally:
        BaseAgent.run = orig
    assert [m.content for m in seen.get("history", [])] == ["earlier"]
