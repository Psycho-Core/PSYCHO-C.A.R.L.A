"""
Round-2 regression tests.

Covers the parity gaps closed in the second pass:

  * per-engine adapters and the in-process ``afm`` engine
  * the A2A protocol (server, client, tool)
  * the workflow engine (graph, stages, conditions, cycles, loops)
  * cross-channel sessions (consolidation, decay, channel linking)
  * the daemon (service files, session keys)
  * OAuth connectors (provider registry, URL building, token handling)
  * presets and agent templates
  * the mining subsystem (graceful degradation)
  * recipes (loading, composition)
  * the desktop GUI launcher and bundle export
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import tempfile
import time
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Per-engine adapters
# --------------------------------------------------------------------------- #
class TestEngineAdapters:
    def test_all_expected_adapters_registered(self):
        from psycho_carla.providers.adapters import available_adapters

        adapters = set(available_adapters())
        for expected in (
            "afm", "apple_fm", "exo", "generic_http", "lemonade", "llamacpp",
            "lmstudio", "mlx", "nexa", "ollama", "openai_compat", "sglang",
            "uzu", "vllm",
        ):
            assert expected in adapters, f"{expected} adapter is missing"

    def test_llamacpp_keeps_its_native_adapter(self):
        """llama.cpp speaks /completion, not /chat/completions.

        The dedicated adapter must win over the generic generated one.
        """
        from psycho_carla.providers.adapters.llamacpp import LlamaCppAdapter
        from psycho_carla.providers.base import build_adapter

        adapter = build_adapter("llamacpp")
        assert isinstance(adapter, LlamaCppAdapter)

    def test_generated_engine_adapters_are_openai_compatible(self):
        from psycho_carla.providers.adapters.engines import ENGINE_SPECS
        from psycho_carla.providers.base import build_adapter

        spec_ids = {spec[0] for spec in ENGINE_SPECS}
        for engine_id in ("vllm", "sglang", "mlx", "lmstudio", "exo", "nexa", "uzu"):
            assert engine_id in spec_ids
            adapter = build_adapter(engine_id)
            assert adapter is not None
            assert adapter.name == engine_id
            assert adapter.base_url.startswith("http://")
            assert adapter.base_url.endswith("/v1")

    def test_normalize_openai_base_url(self):
        """The adapter appends ``/v1``, so a trailing copy is stripped."""
        from psycho_carla.providers.adapters.engines import (
            normalize_openai_base_url,
        )

        assert normalize_openai_base_url("http://127.0.0.1:8000") == (
            "http://127.0.0.1:8000"
        )
        assert normalize_openai_base_url("http://127.0.0.1:8000/v1") == (
            "http://127.0.0.1:8000"
        )
        # A trailing slash must not double up.
        assert normalize_openai_base_url("http://127.0.0.1:8000/v1/") == (
            "http://127.0.0.1:8000"
        )
        # A proxy path prefix is preserved.
        assert normalize_openai_base_url("http://host/proxy/v1") == (
            "http://host/proxy"
        )

    def test_afm_reports_unavailable_without_the_sdk(self):
        from psycho_carla.providers.adapters.afm import afm_available

        # The optional SDK is not a hard dependency: the function answers
        # with (available, reason) rather than raising.
        available, reason = afm_available()
        assert isinstance(available, bool)
        if not available:
            assert reason, "an unavailable engine must explain itself"

    def test_afm_adapter_is_registered_and_importable(self):
        from psycho_carla.providers.adapters.afm import AfmAdapter

        assert AfmAdapter.name == "afm"
        # The in-process engine drives Apple's Foundation Models SDK.
        assert AfmAdapter.supports_streaming is False

    def test_in_process_engines_declares_afm(self):
        from psycho_carla.providers.adapters.engines import IN_PROCESS_ENGINES

        assert "afm" in IN_PROCESS_ENGINES


# --------------------------------------------------------------------------- #
# A2A
# --------------------------------------------------------------------------- #
class TestA2A:
    def test_agent_card_serialises(self):
        from psycho_carla.a2a.protocol import AgentCard

        card = AgentCard(
            name="PSYCHO-C.A.R.L.A.",
            description="A local-first assistant.",
            url="http://127.0.0.1:8787",
            capabilities=["chat", "tools"],
        )
        data = card.to_dict()
        assert data["name"] == "PSYCHO-C.A.R.L.A."
        assert data["capabilities"] == ["chat", "tools"]

    def test_task_states_are_complete(self):
        from psycho_carla.a2a.protocol import TaskState

        names = {state.value for state in TaskState}
        assert {"submitted", "working", "completed", "failed", "canceled"} <= names

    def _card(self):
        from psycho_carla.a2a.protocol import AgentCard

        return AgentCard(
            name="PSYCHO-C.A.R.L.A.",
            description="A local-first assistant.",
            url="http://127.0.0.1:8787",
        )

    def test_server_rejects_a_missing_token(self):
        from psycho_carla.a2a.server import A2AServer

        server = A2AServer(self._card(), auth_token="secret-token")
        assert server.authenticate("wrong-token") is False
        assert server.authenticate(None) is False
        assert server.authenticate("") is False

    def test_server_accepts_the_right_token(self):
        from psycho_carla.a2a.server import A2AServer

        server = A2AServer(self._card(), auth_token="secret-token")
        assert server.authenticate("secret-token") is True

    def test_server_without_a_token_accepts_everything(self):
        from psycho_carla.a2a.server import A2AServer

        server = A2AServer(self._card())
        assert server.authenticate(None) is True

    def test_server_handles_a_task_and_stores_history(self):
        from psycho_carla.a2a.server import A2AServer

        def handler(text: str) -> str:
            return f"ECHO: {text}"

        server = A2AServer(self._card(), handler=handler)
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tasks/send",
                "params": {"message": {"parts": [{"text": "hello"}]}},
            }
        )
        result = response["result"]
        assert result["state"] in {"completed", "working"}
        task_id = result["id"]
        assert task_id

        got = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tasks/get",
                "params": {"id": task_id},
            }
        )
        assert got["result"]["id"] == task_id
        assert task_id in server.tasks

    def test_server_rejects_an_unknown_method(self):
        from psycho_carla.a2a.server import A2AServer

        server = A2AServer(self._card(), handler=lambda text: text)
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "3",
                "method": "tasks/teleport",
                "params": {},
            }
        )
        assert "error" in response
        assert response["error"]["code"] == -32601

    def test_server_reports_an_unauthorised_request(self):
        from psycho_carla.a2a.server import A2AServer

        server = A2AServer(self._card(), auth_token="secret", handler=lambda t: t)
        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": "4",
                "method": "tasks/send",
                "params": {"message": {"parts": [{"text": "hi"}]}},
            },
            token="wrong",
        )
        assert "error" in response
        assert response["error"]["code"] == -32001

    def test_client_tool_returns_a_tool_result(self):
        from psycho_carla.a2a.client import A2AClient
        from psycho_carla.a2a.tool import A2AAgentTool
        from psycho_carla.tools.registry import ToolResult

        tool = A2AAgentTool(A2AClient("http://127.0.0.1:1"), name="remote")
        # The port is closed, so the call fails — but it must fail as a
        # ToolResult, not as an exception.
        result = tool.execute(input="hello")
        assert isinstance(result, ToolResult)
        assert result.ok is False

    def test_client_raises_on_an_unreachable_agent(self):
        from psycho_carla.a2a.client import A2AClient, A2AClientError

        client = A2AClient("http://127.0.0.1:1")
        with pytest.raises(A2AClientError):
            client.discover()


# --------------------------------------------------------------------------- #
# Workflow
# --------------------------------------------------------------------------- #
class TestWorkflow:
    def _graph(self):
        from psycho_carla.workflow import WorkflowBuilder

        builder = WorkflowBuilder("demo")
        builder.add_agent("a", prompt="Do A")
        builder.add_agent("b", prompt="Do B")
        builder.add_tool("t", tool_name="calculator")
        builder.connect("a", "t")
        builder.connect("t", "b")
        return builder.build()

    def test_builder_produces_a_valid_graph(self):
        graph = self._graph()
        valid, message = graph.validate()
        assert valid, message
        assert len(graph.nodes) == 3
        assert len(graph.edges) == 2

    def test_topological_sort_orders_the_nodes(self):
        graph = self._graph()
        order = graph.topological_sort()
        assert order.index("a") < order.index("t") < order.index("b")

    def test_execution_stages_group_independent_nodes(self):
        from psycho_carla.workflow import WorkflowBuilder

        builder = WorkflowBuilder("fan")
        builder.add_agent("x")
        builder.add_agent("y")
        builder.add_agent("z")
        builder.connect("x", "z")
        builder.connect("y", "z")
        stages = builder.build().execution_stages()
        assert stages[0] == ["x", "y"]
        assert stages[1] == ["z"]

    def test_cycles_are_detected_and_rejected(self):
        from psycho_carla.workflow import WorkflowBuilder

        builder = WorkflowBuilder("cyclic")
        builder.add_agent("x")
        builder.add_agent("y")
        builder.connect("x", "y")
        builder.connect("y", "x")
        # build() validates, so a cycle is refused outright.
        with pytest.raises(ValueError, match="Cycle"):
            builder.build()

    def test_condition_evaluator_resolves_references_and_literals(self):
        from psycho_carla.workflow.engine import evaluate_condition

        outputs = {"count": "10", "flag": "yes", "empty": ""}
        assert evaluate_condition("count > 5", outputs) is True
        assert evaluate_condition("count == 10", outputs) is True
        assert evaluate_condition("flag == yes", outputs) is True
        assert evaluate_condition("not empty", outputs) is True
        assert evaluate_condition("empty", outputs) is False
        assert evaluate_condition("count > 5 and flag == yes", outputs) is True
        assert evaluate_condition("count > 100 or flag == yes", outputs) is True

    def test_condition_evaluator_never_execs_python(self):
        """A Python expression must be treated as a plain identifier.

        The evaluator is a restricted parser, not an ``eval``. A token
        that is not a known node output resolves to the empty string, so
        the comparison is False — and nothing is executed.
        """
        from psycho_carla.workflow.engine import evaluate_condition

        marker = "/tmp/carla-should-never-exist"
        import os

        if os.path.exists(marker):  # pragma: no cover - safety net
            os.remove(marker)
        # The expression contains a call; if it were evaluated it would
        # create the file.
        expr = f"__import__('os').system('touch {marker}')"
        assert evaluate_condition(expr, {}) is False
        assert not os.path.exists(marker)

    def test_condition_evaluator_treats_unknown_nodes_as_empty(self):
        from psycho_carla.workflow.engine import evaluate_condition

        # A node that produced nothing is falsy as a bare reference.
        assert evaluate_condition("missing", {}) is False
        # An explicit braced reference to a missing node is empty too.
        assert evaluate_condition("{missing}", {}) is False

    def test_toml_workflow_round_trips(self):
        from psycho_carla.workflow import load_workflow

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wf.toml"
            path.write_text(
                "[workflow]\nname = 'sample'\n"
                "[[workflow.nodes]]\nid = 'one'\ntype = 'agent'\n"
                "agent = 'orchestrator'\nprompt = 'Do {input}'\n",
                encoding="utf-8",
            )
            graph = load_workflow(path)
            assert graph.name == "sample"
            assert len(graph.nodes) == 1
            assert graph.nodes[0].prompt == "Do {input}"

    def test_example_workflows_are_valid(self):
        from psycho_carla.workflow import load_workflow

        examples = Path(__file__).resolve().parents[1] / "examples" / "workflows"
        files = sorted(examples.glob("*.toml"))
        assert files, "no example workflows found"
        for path in files:
            graph = load_workflow(path)
            valid, message = graph.validate()
            assert valid, f"{path.name}: {message}"


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
class TestSessions:
    def _store(self, tmp_path: Path):
        from psycho_carla.sessions import SessionStore

        return SessionStore(tmp_path / "sessions.db")

    def test_create_and_reload(self, tmp_path):
        from psycho_carla.sessions import SessionIdentity

        store = self._store(tmp_path)
        identity = SessionIdentity(user_id="u1", display_name="Boss")
        session = store.create(identity=identity)
        store.add_message(session.session_id, "user", "hello")
        store.add_message(session.session_id, "assistant", "hi")

        loaded = store.get(session.session_id)
        assert loaded is not None
        assert loaded.message_count == 2
        assert loaded.identity.display_name == "Boss"

    def test_channels_link_to_the_same_session(self, tmp_path):
        store = self._store(tmp_path)
        session = store.create()
        store.link_channel(session.session_id, "hud", "u1")
        store.link_channel(session.session_id, "cli", "u1")

        found = store.find_by_channel("cli", "u1")
        assert found is not None
        assert found.session_id == session.session_id

    def test_consolidation_replaces_old_messages_with_an_anchor(self, tmp_path):
        store = self._store(tmp_path)
        store._consolidation_threshold = 4
        session = store.create()
        for i in range(8):
            store.add_message(session.session_id, "user", f"Message {i}: the fox")

        report = store.consolidate(session.session_id)
        assert report["consolidated"] > 0
        assert len(store.anchors(session.session_id)) == 1
        assert store.get(session.session_id).message_count < 8

    def test_context_includes_anchors(self, tmp_path):
        store = self._store(tmp_path)
        store._consolidation_threshold = 2
        session = store.create()
        for i in range(4):
            store.add_message(session.session_id, "user", f"Message {i}")
        store.consolidate(session.session_id)

        context = store.context(session.session_id)
        assert context
        assert context[0]["role"] == "system"

    def test_expiry_removes_the_session_and_its_children(self, tmp_path):
        store = self._store(tmp_path)
        store._consolidation_threshold = 2
        session = store.create()
        for i in range(4):
            store.add_message(session.session_id, "user", f"Message {i}")
        store.consolidate(session.session_id)

        expired = store.expire(max_age_hours=0)
        assert expired == 1
        stats = store.stats()
        assert stats["sessions"] == 0
        assert stats["messages"] == 0
        assert stats["anchors"] == 0

    def test_compression_keeps_the_signal(self):
        from psycho_carla.sessions.compression import consolidate_messages

        messages = [
            {"role": "user", "content": "ok"},
            {"role": "user", "content": "The decision was to use Postgres because it is proven."},
            {"role": "assistant", "content": "Understood."},
        ]
        summary = consolidate_messages(messages)
        assert "Postgres" in summary
        assert "Understood" not in summary

    def test_compression_estimate_tokens(self):
        from psycho_carla.sessions.compression import estimate_tokens

        assert estimate_tokens([{"content": "a" * 400}]) >= 90


# --------------------------------------------------------------------------- #
# Daemon
# --------------------------------------------------------------------------- #
class TestDaemon:
    def test_session_key_shape(self):
        from psycho_carla.daemon import GatewayDaemon

        key = GatewayDaemon.session_key("hud", "direct", "boss", None)
        assert key == "agent:main:hud:direct:boss:None"

    def test_session_key_includes_the_thread(self):
        from psycho_carla.daemon import GatewayDaemon

        key = GatewayDaemon.session_key("slack", "channel", "C123", "T456")
        assert key == "agent:main:slack:channel:C123:T456"

    def test_systemd_unit_is_generated(self, tmp_path):
        from psycho_carla.daemon import generate_systemd_service

        path = tmp_path / "psycho-carla.service"
        content = generate_systemd_service(path)
        assert path.exists()
        assert "[Unit]" in content
        assert "ExecStart=" in content
        assert "psycho_carla" in content

    def test_launchd_plist_is_generated(self, tmp_path):
        from psycho_carla.daemon import generate_launchd_plist

        path = tmp_path / "com.psycho.carla.console.plist"
        content = generate_launchd_plist(path)
        assert path.exists()
        assert "<plist" in content
        assert "com.psycho.carla.console" in content

    def test_windows_task_is_generated(self, tmp_path):
        from psycho_carla.daemon import generate_windows_task

        path = tmp_path / "install-task.ps1"
        content = generate_windows_task(path)
        assert path.exists()
        assert "Register-ScheduledTask" in content
        assert "psycho-carla" in content

    def test_service_instructions_exist_per_platform(self):
        from psycho_carla.daemon import service_instructions

        instructions = service_instructions()
        for platform in ("linux", "darwin", "windows"):
            assert platform in instructions
            assert instructions[platform]


# --------------------------------------------------------------------------- #
# Connectors / OAuth
# --------------------------------------------------------------------------- #
class TestConnectors:
    def test_provider_registry_covers_the_expected_providers(self):
        from psycho_carla.connectors import OAUTH_PROVIDERS

        for name in ("google", "strava", "spotify"):
            assert name in OAUTH_PROVIDERS

    def test_google_provider_covers_the_google_connectors(self):
        from psycho_carla.connectors import OAUTH_PROVIDERS

        provider = OAUTH_PROVIDERS["google"]
        assert "gmail" in provider.connector_ids
        assert "gdrive" in provider.connector_ids
        assert any("drive" in scope for scope in provider.scopes)

    def test_spotify_uses_basic_auth_and_its_own_port(self):
        from psycho_carla.connectors import OAUTH_PROVIDERS

        provider = OAUTH_PROVIDERS["spotify"]
        assert provider.token_auth == "basic"
        assert provider.callback_port == 8888

    def test_provider_lookup_by_connector(self):
        from psycho_carla.connectors import get_provider_for_connector

        assert get_provider_for_connector("gmail").name == "google"
        assert get_provider_for_connector("nope") is None

    def test_auth_url_carries_the_required_parameters(self):
        from psycho_carla.connectors import OAUTH_PROVIDERS, build_auth_url

        provider = OAUTH_PROVIDERS["google"]
        url = build_auth_url(provider, "CLIENT-ID", state="abc")
        assert url.startswith(provider.auth_endpoint)
        assert "client_id=CLIENT-ID" in url
        assert "response_type=code" in url
        assert "state=abc" in url
        assert "scope=" in url

    def test_an_error_shaped_token_response_is_rejected(self):
        from psycho_carla.connectors import require_access_token

        with pytest.raises(RuntimeError):
            require_access_token({"error": "invalid_grant"})
        with pytest.raises(RuntimeError):
            require_access_token("not a dict")

    def test_a_good_token_response_is_accepted(self):
        from psycho_carla.connectors import require_access_token

        assert require_access_token({"access_token": " abc "}) == "abc"

    def test_credentials_are_written_owner_only(self, tmp_path):
        from psycho_carla.connectors import save_tokens

        path = tmp_path / "google.json"
        save_tokens(path, {"access_token": "secret"})
        if os.name != "nt":
            assert oct(path.stat().st_mode & 0o777) == "0o600"

    def test_tokens_round_trip(self, tmp_path):
        from psycho_carla.connectors import load_tokens, save_tokens

        path = tmp_path / "google.json"
        save_tokens(path, {"access_token": "secret", "refresh_token": "r"})
        loaded = load_tokens(path)
        assert loaded["access_token"] == "secret"

    def test_knowledge_store_indexes_and_searches(self, tmp_path):
        from psycho_carla.connectors import KnowledgeStore
        from psycho_carla.connectors.base import Document

        store = KnowledgeStore(tmp_path / "k.db")
        store.add(
            Document(
                doc_id="1", source="gmail", title="Quarterly report",
                body="Revenue grew sharply this quarter across all regions.",
            )
        )
        store.add(
            Document(
                doc_id="2", source="gdrive", title="Meeting notes",
                body="We discussed the hiring plan and the roadmap.",
            )
        )
        hits = store.search("revenue")
        assert hits
        assert hits[0].doc_id == "1"
        assert store.count() == 2
        assert store.sources() == {"gmail": 1, "gdrive": 1}

    def test_connector_status_for_an_unknown_connector(self):
        from psycho_carla.connectors import connector_status

        status = connector_status("nope")
        assert status["connected"] is False


# --------------------------------------------------------------------------- #
# Presets and templates
# --------------------------------------------------------------------------- #
class TestPresetsAndTemplates:
    def test_all_seven_presets_are_present(self):
        from psycho_carla.templates.presets import available_presets

        presets = set(available_presets())
        for expected in (
            "morning-digest-mac", "morning-digest-linux", "morning-digest-minimal",
            "deep-research", "code-assistant", "scheduled-monitor", "chat-simple",
        ):
            assert expected in presets

    def test_every_preset_loads(self):
        from psycho_carla.templates.presets import load_preset

        for name in ("morning-digest-mac", "deep-research", "chat-simple"):
            preset = load_preset(name)
            assert preset.name == name
            assert preset.description

    def test_presets_install_to_a_file(self, tmp_path):
        from psycho_carla.templates.presets import install_preset

        out = tmp_path / "config.toml"
        install_preset("chat-simple", out)
        assert out.exists()
        assert "[preset]" in out.read_text(encoding="utf-8")

    def test_presets_parse_as_valid_config(self, tmp_path):
        from psycho_carla.core.config import load_config
        from psycho_carla.templates.presets import available_presets, install_preset

        for name in available_presets():
            out = tmp_path / f"{name}.toml"
            install_preset(name, out)
            cfg = load_config(out)
            assert cfg is not None

    def test_all_sixteen_templates_are_present(self):
        from psycho_carla.templates.agent_templates import available_templates

        templates = set(available_templates())
        for expected in (
            "architect", "assistant", "code-reviewer", "data-analyst", "debugger",
            "deep-researcher", "fact-checker", "inbox-triager", "meeting-prep",
            "monitor", "note-taker", "security-auditor", "summarizer",
            "translator", "tutor", "writer",
        ):
            assert expected in templates

    def test_every_template_loads_with_a_prompt(self):
        from psycho_carla.templates.agent_templates import load_template_dir

        catalogue = load_template_dir()
        assert len(catalogue) == 16
        for name, template in catalogue.items():
            assert template.name == name
            assert template.description
            assert template.system_prompt
            assert template.max_turns > 0

    def test_template_tools_are_real_tool_names(self):
        from psycho_carla.templates.agent_templates import load_template_dir

        known = {
            "calculator", "code_execute", "file_delete", "file_info", "file_list",
            "file_move", "file_read", "file_search", "file_write", "memory_delete",
            "memory_list", "memory_search", "memory_store", "shell_environment",
            "shell_exec", "shell_python", "shell_which", "skill_list", "skill_load",
            "skill_search", "web_fetch", "web_search",
        }
        for template in load_template_dir().values():
            for tool in template.tools:
                assert tool in known, f"{template.name} names an unknown tool: {tool}"


# --------------------------------------------------------------------------- #
# Mining
# --------------------------------------------------------------------------- #
class TestMining:
    def test_providers_register(self):
        from psycho_carla.mining.providers import ensure_registered

        ensure_registered()
        from psycho_carla.mining import available_providers

        providers = set(available_providers())
        assert {"cpu-pearl", "vllm-pearl", "apple-mps-pearl"} <= providers

    def test_detection_never_raises(self):
        from psycho_carla.mining import detect_all
        from psycho_carla.mining.providers import ensure_registered

        ensure_registered()

        class HW:
            platform = "Linux-6.1-x86_64-with-glibc2.36"

        capabilities = detect_all(HW, "ollama", "model")
        assert capabilities
        for cap in capabilities.values():
            assert isinstance(cap.supported, bool)

    def test_vllm_provider_requires_the_vllm_engine(self):
        from psycho_carla.mining.providers import VllmPearlProvider

        class HW:
            platform = "linux"

        caps = VllmPearlProvider.detect(HW, "ollama", "model")
        assert caps.supported is False
        assert "vLLM" in (caps.reason or "")

    def test_apple_provider_requires_macos(self):
        from psycho_carla.mining.providers import AppleMpsPearlProvider

        class HW:
            platform = "linux"

        caps = AppleMpsPearlProvider.detect(HW, "ollama", "model")
        assert caps.supported is False

    def test_mining_stats_serialise(self):
        from psycho_carla.mining import MiningStats

        stats = MiningStats(provider_id="cpu-pearl", shares_submitted=10,
                            shares_accepted=9)
        data = stats.to_dict()
        assert data["provider_id"] == "cpu-pearl"
        assert data["acceptance_rate"] == 0.9


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #
class TestRecipes:
    def test_recipes_are_discoverable(self):
        from psycho_carla.recipes import discover_recipes

        recipes = discover_recipes()
        assert "general-assistant" in recipes
        assert "research-assistant" in recipes
        assert "monitor-assistant" in recipes

    def test_every_recipe_loads(self):
        from psycho_carla.recipes import discover_recipes, resolve_recipe

        for name in discover_recipes():
            recipe = resolve_recipe(name)
            assert recipe is not None, name
            assert recipe.name
            assert recipe.kind in {"discrete", "operator"}

    def test_operator_recipes_carry_a_schedule(self):
        from psycho_carla.recipes import resolve_recipe

        recipe = resolve_recipe("monitor-assistant")
        assert recipe.schedule_value
        assert recipe.channels

    def test_recipe_composes_into_config_overrides(self):
        from psycho_carla.recipes import recipe_to_config_overrides, resolve_recipe

        recipe = resolve_recipe("coding-assistant")
        overrides = recipe_to_config_overrides(recipe)
        assert "agents" in overrides
        assert overrides["agents"]["coding-assistant"]["tools"]

    def test_recipe_composes_into_an_eval_suite(self):
        from psycho_carla.recipes import recipe_to_eval_suite, resolve_recipe

        recipe = resolve_recipe("general-assistant")
        suite = recipe_to_eval_suite(recipe)
        assert suite is not None
        assert len(suite.cases) == 1


# --------------------------------------------------------------------------- #
# GUI
# --------------------------------------------------------------------------- #
class TestGui:
    def test_hud_url_is_loopback(self):
        from psycho_carla.gui import hud_url

        assert hud_url("127.0.0.1", 8787) == "http://127.0.0.1:8787/"
        # A 0.0.0.0 bind is still reached over loopback.
        assert "127.0.0.1" in hud_url("0.0.0.0", 8787)

    def test_server_probe_reports_the_state(self):
        from psycho_carla.gui import gui_status

        status = gui_status("127.0.0.1", 1)
        assert status["reachable"] is False
        assert status["url"].startswith("http://")

    def test_desktop_bundle_is_self_contained(self, tmp_path):
        from psycho_carla.gui import export_desktop_bundle

        out = export_desktop_bundle(tmp_path / "hud.html")
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert len(html) > 1000
        # No external stylesheet or script references may remain.
        assert 'rel="stylesheet"' not in html
        assert "<script src=" not in html

    def test_desktop_bundle_keeps_the_css_variables(self, tmp_path):
        """The palette must stay in CSS variables so it can be re-themed."""
        from psycho_carla.gui import export_desktop_bundle

        out = export_desktop_bundle(tmp_path / "hud.html")
        html = out.read_text(encoding="utf-8")
        assert ":root" in html
        assert "--" in html


# --------------------------------------------------------------------------- #
# Config additions
# --------------------------------------------------------------------------- #
class TestRound2Config:
    def test_sessions_config_fields(self):
        from psycho_carla.core.config import SessionsConfig

        sessions = SessionsConfig()
        assert sessions.enabled is True
        assert sessions.max_age_hours == 24.0
        assert sessions.consolidation_threshold == 100

    def test_cron_config_parses_array_jobs(self, tmp_path):
        from psycho_carla.core.config import load_config

        path = tmp_path / "c.toml"
        path.write_text(
            '[[cron]]\nname = "digest"\nschedule = "30 7 * * *"\n'
            'agent = "orchestrator"\nprompt = "Brief me."\n',
            encoding="utf-8",
        )
        cfg = load_config(path)
        assert len(cfg.cron.jobs) == 1
        assert cfg.cron.jobs[0].schedule == "30 7 * * *"
        assert cfg.cron.jobs[0].name == "digest"

    def test_server_config_has_a2a_fields(self):
        from psycho_carla.core.config import ServerConfig

        server = ServerConfig()
        assert hasattr(server, "a2a_enabled")
        assert hasattr(server, "a2a_agent")
        assert hasattr(server, "a2a_token")

    def test_sessions_db_path_derives_from_the_home(self):
        from psycho_carla.core.config import CarlaConfig

        cfg = CarlaConfig()
        assert cfg.sessions_db.parent == cfg.data_dir

    def test_default_config_parses_and_has_no_duplicate_tables(self):
        """The generated TOML must parse, and each table must hold its
        own keys — a misplaced section header silently swallows the
        sections below it."""
        import tomllib

        from psycho_carla.config_template import render_default_config

        raw = render_default_config()
        data = tomllib.loads(raw)

        for section in ("server", "sessions", "cron", "skills", "voice"):
            assert section in data, f"{section} is missing from the template"

        # The server's keys must be the server's keys, not a neighbour's.
        assert data["server"]["port"] == 8787
        assert "require_auth" in data["server"]
        assert "a2a_enabled" in data["server"]
        # The sessions table must not have swallowed the server's keys.
        assert "host" not in data["sessions"]
        assert "port" not in data["sessions"]

    def test_default_config_loads_through_the_loader(self, tmp_path):
        from psycho_carla.core.config import load_config
        from psycho_carla.config_template import render_default_config

        path = tmp_path / "config.toml"
        path.write_text(render_default_config(), encoding="utf-8")
        cfg = load_config(path)
        assert cfg.server.port == 8787
        assert cfg.sessions.enabled is True
        assert cfg.cron.enabled is True


# --------------------------------------------------------------------------- #
# The vault
# --------------------------------------------------------------------------- #
class TestVault:
    def test_encryption_is_available_or_explains_itself(self):
        from psycho_carla.security import encryption_available

        available, reason = encryption_available()
        assert isinstance(available, bool)
        if not available:
            assert reason

    def test_a_secret_round_trips(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault

        vault = Vault(tmp_path / "vault.enc")
        vault.set("api_key", "sk-secret")
        assert vault.get("api_key") == "sk-secret"
        assert "api_key" in vault
        assert vault.keys() == ["api_key"]

    def test_the_secret_is_not_on_disk_in_plaintext(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault

        vault = Vault(tmp_path / "vault.enc")
        vault.set("api_key", "sk-plaintext-marker")
        raw = (tmp_path / "vault.enc").read_bytes()
        assert b"sk-plaintext-marker" not in raw

    def test_the_vault_file_is_owner_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault

        vault = Vault(tmp_path / "vault.enc")
        vault.set("api_key", "sk-secret")
        if os.name != "nt":
            assert oct((tmp_path / "vault.enc").stat().st_mode & 0o777) == "0o600"
            key = tmp_path / "vault.key"
            if key.exists():
                assert oct(key.stat().st_mode & 0o777) == "0o600"

    def test_remove_deletes_the_secret(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault

        vault = Vault(tmp_path / "vault.enc")
        vault.set("api_key", "sk-secret")
        assert vault.remove("api_key") is True
        assert vault.remove("api_key") is False
        assert vault.get("api_key") == ""

    def test_an_empty_key_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault, VaultError

        vault = Vault(tmp_path / "vault.enc")
        with pytest.raises(VaultError):
            vault.set("   ", "value")

    def test_a_wrong_key_cannot_decrypt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        from psycho_carla.security import Vault, VaultError

        vault = Vault(tmp_path / "vault.enc")
        vault.set("api_key", "sk-secret")
        # A different master key must not silently produce garbage.
        monkeypatch.setenv("PSYCHO_CARLA_VAULT_KEY",
                           base64.b64encode(b"0" * 32).decode("ascii"))
        fresh = Vault(tmp_path / "vault.enc")
        with pytest.raises(VaultError):
            fresh.get("api_key")


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
class TestCliWiring:
    def test_every_top_level_command_parses(self):
        from psycho_carla.cli.main import build_parser

        parser = build_parser()
        # Commands that take a required positional, with a value for it.
        commands = [
            ("init", []), ("doctor", []), ("ask", ["hello"]), ("chat", []),
            ("serve", []), ("agents", []), ("tiers", []), ("memory", ["stats"]),
            ("cron", ["list"]), ("optimize", []), ("bench", []), ("eval", []),
            ("voice", ["status"]), ("vault", ["list"]), ("tools", ["list"]),
            ("gui", ["status"]), ("mine", ["doctor"]), ("recipes", ["list"]),
            ("compose", ["list"]), ("sessions", ["stats"]),
            ("templates", ["list"]), ("connect", ["list"]),
            ("daemon", ["status"]), ("workflow", ["list"]), ("auth", ["status"]),
            ("skill", ["list"]), ("mcp", ["status"]), ("config", ["path"]),
            ("tunnel", ["status"]), ("registry", ["list"]),
            ("feedback", ["stats"]),
        ]
        for command, extra in commands:
            args = parser.parse_args([command, *extra])
            assert args.command == command, command
            assert hasattr(args, "func"), command

    def test_nested_subcommands_keep_the_top_level_command(self):
        """A sub-subparser must not overwrite the top-level `command`.

        This is why the MCP `--command` option is `--exec`: an option
        named `command` collides with the subparser's dest.
        """
        from psycho_carla.cli.main import build_parser

        parser = build_parser()
        for argv in (
            ["mcp", "add", "github", "--key", "k"],
            ["mcp", "status"],
            ["vault", "list"],
            ["tools", "list"],
            ["workflow", "list"],
            ["connect", "login", "gmail"],
        ):
            args = parser.parse_args(argv)
            assert args.command == argv[0], argv

    def test_the_mcp_add_option_is_not_named_command(self):
        from psycho_carla.cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["mcp", "add", "github", "--exec", "npx"])
        assert args.command == "mcp"
        assert args.exec_command == "npx"

    def test_mcp_presets_are_well_formed(self):
        from psycho_carla.cli.main import MCP_PRESETS

        for name, preset in MCP_PRESETS.items():
            assert preset["transport"] in {"stdio", "http", "sse"}, name
            assert preset.get("command") or preset.get("url"), name

    def test_the_token_variables_match_the_presets(self):
        from psycho_carla.cli.main import _MCP_TOKEN_VARS, MCP_PRESETS

        for name in _MCP_TOKEN_VARS:
            assert name in MCP_PRESETS, name


# --------------------------------------------------------------------------- #
# Energy summary semantics
# --------------------------------------------------------------------------- #
class TestEnergySummary:
    def test_an_unmeasured_window_reports_none_not_zero(self, tmp_path):
        """An unmeasured joule count is not zero joules."""
        from psycho_carla.core.telemetry import Telemetry

        telemetry = Telemetry(tmp_path / "t.db")
        summary = telemetry.energy_summary(window_minutes=60)
        assert summary["total_joules"] is None
        assert summary["total_flops"] is None
        assert summary["avg_watts"] is None
        assert summary["models"] == []

    def test_a_measured_window_reports_totals(self, tmp_path):
        from psycho_carla.core.telemetry import RequestMetric, Telemetry

        telemetry = Telemetry(tmp_path / "t.db")
        telemetry.record_request(
            RequestMetric(
                request_id="r1",
                agent="orchestrator",
                tier="fast_tier",
                provider="openai_compat",
                model="test-model",
                started_at=time.time(),
                latency_ms=100.0,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                energy_joules=2.5,
                flops=1e12,
                power_watts=250.0,
            )
        )
        summary = telemetry.energy_summary(window_minutes=60)
        assert summary["total_joules"] == 2.5
        assert summary["total_flops"] == 1e12
        assert summary["avg_watts"] == 250.0
        assert summary["requests"] == 1
        assert summary["models"][0]["model"] == "test-model"

    def test_partial_measurement_is_honest_per_field(self, tmp_path):
        """FLOPs stay None when the parameter count was unknown."""
        from psycho_carla.core.telemetry import RequestMetric, Telemetry

        telemetry = Telemetry(tmp_path / "t.db")
        telemetry.record_request(
            RequestMetric(
                request_id="r1",
                agent="orchestrator",
                tier="fast_tier",
                provider="openai_compat",
                model="test-model",
                started_at=time.time(),
                latency_ms=100.0,
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                energy_joules=2.5,
                flops=0.0,
                power_watts=250.0,
            )
        )
        summary = telemetry.energy_summary(window_minutes=60)
        assert summary["total_joules"] == 2.5
        assert summary["total_flops"] is None
        assert summary["avg_watts"] == 250.0


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #
class TestPackaging:
    """The data files must survive a wheel build.

    A package-data glob that misses a directory ships a broken install:
    ``carla templates list`` would return nothing from a clean venv.
    """

    def _pyproject(self) -> str:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        return (root / "pyproject.toml").read_text(encoding="utf-8")

    def test_package_data_covers_every_data_directory(self):
        content = self._pyproject()
        for pattern in (
            "templates/data/*.toml",
            "templates/presets/*.toml",
            "recipes/data/*.toml",
            "examples/workflows/*.toml",
            "server/web/static/*.css",
            "server/web/static/*.js",
            "server/web/templates/*.html",
        ):
            assert pattern in content, f"{pattern} is not in package-data"

    def test_the_bundled_data_is_present_in_the_source_tree(self):
        from psycho_carla.examples import skills_dir, workflows_dir
        from psycho_carla.recipes import discover_recipes
        from psycho_carla.templates.agent_templates import available_templates
        from psycho_carla.templates.presets import available_presets

        assert len(available_templates()) == 16
        assert len(available_presets()) == 7
        assert len(discover_recipes()) == 7
        assert len(list(workflows_dir().glob("*.toml"))) == 2
        assert (skills_dir() / "status-report" / "SKILL.md").exists()

    def test_the_examples_package_is_importable(self):
        from psycho_carla.examples import examples_dir

        assert examples_dir().is_dir()

    def test_the_console_script_is_declared(self):
        content = self._pyproject()
        assert 'carla = "psycho_carla.cli.main:main"' in content


class TestModelManager:
    """The model catalog and downloader (round 5)."""

    def test_the_catalog_has_entries(self):
        from psycho_carla.intelligence.model_catalog import list_models

        specs = list_models()
        assert len(specs) >= 10
        assert all(s.model_id for s in specs)

    def test_a_catalog_entry_carries_every_field_the_gui_needs(self):
        from psycho_carla.intelligence.model_catalog import find_model_spec

        spec = find_model_spec("qwen3:4b")
        assert spec is not None
        assert spec.ram_gb > 0
        assert spec.disk_size_gb() > 0
        assert spec.hf_repo
        assert spec.gguf_file.endswith(".gguf")
        assert "ollama" in spec.supported_engines

    def test_lookup_falls_back_to_a_prefix_match(self):
        from psycho_carla.intelligence.model_catalog import find_model_spec

        assert find_model_spec("qwen3") is not None
        assert find_model_spec("definitely-not-a-model") is None
        assert find_model_spec("") is None

    def test_recommendations_only_include_models_that_fit_the_ram(self):
        from psycho_carla.intelligence.model_catalog import recommend_models

        specs = recommend_models(3.0, limit=10)
        assert specs
        assert all(s.ram_gb <= 3.0 for s in specs)

    def test_recommendations_are_ordered_best_first(self):
        from psycho_carla.intelligence.model_catalog import recommend_models

        specs = recommend_models(32.0, limit=5)
        params = [s.parameter_count_b for s in specs]
        assert params == sorted(params, reverse=True)

    def test_engine_specific_identifiers_are_resolved(self):
        from psycho_carla.intelligence.model_catalog import (
            resolve_model_id_for_engine,
        )

        gguf = resolve_model_id_for_engine("qwen3:4b", "llamacpp")
        assert gguf.endswith(".gguf")
        assert resolve_model_id_for_engine("qwen3:4b", "ollama") == "qwen3:4b"

    def test_the_engine_filter_narrows_the_catalog(self):
        from psycho_carla.intelligence.model_catalog import list_models

        mlx_free = list_models(engine="ollama")
        assert mlx_free
        assert all("ollama" in s.supported_engines for s in mlx_free)

    def test_a_model_spec_round_trips_through_a_dict(self):
        from psycho_carla.intelligence.model_catalog import find_model_spec

        data = find_model_spec("qwen3:4b").to_dict()
        assert data["model_id"] == "qwen3:4b"
        assert isinstance(data["supported_engines"], list)

    def test_pulling_for_an_unknown_engine_explains_itself(self):
        from psycho_carla.intelligence.model_manager import pull_model

        result = pull_model("qwen3:4b", "some-unknown-engine")
        assert not result.ok
        assert result.message

    def test_pulling_for_vllm_defers_to_the_server(self):
        from psycho_carla.intelligence.model_manager import pull_model

        result = pull_model("qwen3:4b", "vllm")
        assert result.ok
        assert "automatically" in result.message

    def test_mlx_is_refused_with_a_reason(self):
        from psycho_carla.intelligence.model_manager import pull_model

        result = pull_model("qwen3:4b", "mlx")
        assert not result.ok
        assert "macOS" in result.message

    def test_an_unreachable_ollama_gives_an_actionable_message(self):
        from psycho_carla.intelligence.model_manager import pull_model

        result = pull_model("qwen3:0.6b", "ollama")
        # Either it connected, or it told us how to start Ollama.
        if not result.ok:
            assert "ollama" in result.message.lower()

    def test_local_models_lists_what_is_on_disk(self):
        from psycho_carla.intelligence.model_manager import local_models

        entries = local_models()
        assert isinstance(entries, list)
        for entry in entries:
            assert entry["model_id"]
            assert entry["engine"] in ("ollama", "llamacpp", "native_onnx")

    def test_deleting_a_model_outside_the_models_directory_is_refused(self):
        from psycho_carla.intelligence.model_manager import delete_local_model

        result = delete_local_model("definitely-not-a-model", "llamacpp")
        assert not result.ok

    def test_the_paths_helper_honours_the_environment_override(self, tmp_path,
                                                                monkeypatch):
        from psycho_carla.utils.paths import carla_home, models_dir

        monkeypatch.setenv("PSYCHO_CARLA_HOME", str(tmp_path))
        assert str(carla_home()).startswith(str(tmp_path))
        assert str(models_dir()).startswith(str(tmp_path))


class TestDesktopApp:
    """The native desktop shell (round 5)."""

    def test_the_desktop_module_imports(self):
        from psycho_carla import desktop

        assert hasattr(desktop, "DesktopApp")
        assert hasattr(desktop, "main")

    def test_the_desktop_entry_point_is_importable_without_pywebview(self):
        # pywebview is optional — importing the module must not require it.
        from psycho_carla.desktop import DesktopApp

        app = DesktopApp(port=9999)
        assert app.host
        assert app.port == 9999

    def test_the_spec_file_exists_for_windows_packaging(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        spec = root / "PsychoCARLA.spec"
        assert spec.exists()
        content = spec.read_text(encoding="utf-8")
        assert "PsychoCARLA" in content
        # The voice stack must be opt-in, not bundled by default.
        assert "CARLA_BUILD_VOICE" in content
        assert "console=False" in content

    def test_the_hud_exposes_the_model_manager_routes(self):
        from psycho_carla.server.api.hud import router

        paths = {r.path for r in router.routes}
        assert "/models" in paths
        assert "/models/local" in paths
        assert "/models/recommend" in paths
        assert "/models/pull" in paths


class TestChannels:
    """The channel package and the bridge (round 6)."""

    def test_every_upstream_channel_name_is_registered(self):
        from psycho_carla.channels import available_channels

        registered = set(available_channels())
        upstream = {
            "bluebubbles", "discord_channel", "email_channel", "feishu",
            "gmail", "google_chat", "imessage_daemon", "irc_channel",
            "line_channel", "mastodon_channel", "matrix_channel", "mattermost",
            "messenger_channel", "nostr_channel", "reddit_channel",
            "rocketchat_channel", "sendblue", "signal_channel", "slack",
            "slack_daemon", "teams", "telegram", "twilio_sms", "twitch_channel",
            "twitter_channel", "viber_channel", "webchat", "webhook",
            "whatsapp", "whatsapp_baileys", "xmpp_channel", "zulip_channel",
        }
        missing = upstream - registered
        assert not missing, f"missing channel names: {sorted(missing)}"

    def test_there_are_at_least_thirty_channels(self):
        from psycho_carla.channels import available_channels

        assert len(available_channels()) >= 30

    def test_every_registered_channel_implements_the_contract(self):
        from psycho_carla.channels import ChannelRegistry

        for channel_id, klass in ChannelRegistry.all().items():
            for method in ("connect", "disconnect", "send", "status",
                           "list_channels", "on_message"):
                assert hasattr(klass, method), f"{channel_id} lacks {method}"

    def test_a_channel_can_be_created_and_described(self):
        from psycho_carla.channels import create_channel

        adapter = create_channel("telegram", {"token": "abc"})
        assert adapter is not None
        assert adapter.channel_id == "telegram"
        description = adapter.describe()
        assert description["channel_id"] == "telegram"
        assert description["configured"] is True

    def test_a_channel_without_credentials_reports_what_is_missing(self):
        from psycho_carla.channels import create_channel

        adapter = create_channel("telegram", {})
        assert adapter.is_configured() is False
        assert "token" in adapter.missing_credentials()

    def test_the_registry_creates_an_adapter_by_id(self):
        from psycho_carla.channels import ChannelRegistry, create_channel

        assert ChannelRegistry.get("telegram") is not None
        assert create_channel("not-a-real-channel") is None

    def test_the_bridge_routes_a_message_to_the_runtime(self):
        from psycho_carla.channels import ChannelBridge
        from psycho_carla.channels.base import BaseChannel, ChannelMessage

        class Echo(BaseChannel):
            channel_id = "echo"

            def connect(self): pass
            def disconnect(self): pass
            def send(self, channel, content, **kw): return True
            def status(self): return self._status
            def list_channels(self): return ["dst"]
            def handle_event(self, payload): return None

        class FakeRuntime:
            def ask(self, prompt, **kwargs):
                return {"text": "Reply from the agent."}

        bridge = ChannelBridge({"echo": Echo()}, runtime=FakeRuntime())
        reply = bridge.handle_incoming(
            ChannelMessage(channel="echo", sender="alice", content="hello")
        )
        assert reply == "Reply from the agent."

    def test_the_bridge_handles_the_help_command(self):
        from psycho_carla.channels import ChannelBridge
        from psycho_carla.channels.base import BaseChannel, ChannelMessage

        class Echo(BaseChannel):
            channel_id = "echo"
            def connect(self): pass
            def disconnect(self): pass
            def send(self, channel, content, **kw): return True
            def status(self): return self._status
            def list_channels(self): return []
            def handle_event(self, payload): return None

        bridge = ChannelBridge({"echo": Echo()})
        reply = bridge.handle_incoming(
            ChannelMessage(channel="echo", sender="a", content="/help")
        )
        assert reply
        assert "/agents" in reply

    def test_the_bridge_falls_back_to_chat_for_unknown_commands(self):
        from psycho_carla.channels import ChannelBridge
        from psycho_carla.channels.base import BaseChannel, ChannelMessage

        class Echo(BaseChannel):
            channel_id = "echo"
            def connect(self): pass
            def disconnect(self): pass
            def send(self, channel, content, **kw): return True
            def status(self): return self._status
            def list_channels(self): return []
            def handle_event(self, payload): return None

        class FakeRuntime:
            def ask(self, prompt, **kwargs):
                return {"text": "treated as chat"}

        bridge = ChannelBridge({"echo": Echo()}, runtime=FakeRuntime())
        reply = bridge.handle_incoming(
            ChannelMessage(channel="echo", sender="a", content="/unknown")
        )
        assert reply == "treated as chat"

    def test_the_webhook_channel_verifies_a_signature(self):
        from psycho_carla.channels.generic import WebhookChannel
        import hmac
        import hashlib

        adapter = WebhookChannel({"secret": "s3cret"})
        body = b'{"text":"hi"}'
        good = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        assert adapter.verify_signature(body, good) is True
        assert adapter.verify_signature(body, "wrong") is False

    def test_the_webhook_channel_handles_an_event(self):
        from psycho_carla.channels.generic import WebhookChannel

        adapter = WebhookChannel({})
        adapter.on_message(lambda m: f"echo: {m.content}")
        reply = adapter.handle_event({"text": "ping", "sender": "alice"})
        assert reply == "echo: ping"

    def test_the_webhook_channel_rejects_an_unknown_sender(self):
        from psycho_carla.channels.generic import WebhookChannel

        adapter = WebhookChannel({"allowed_senders": ["alice"]})
        adapter.on_message(lambda m: "ok")
        assert adapter.handle_event({"text": "hi", "sender": "mallory"}) is None

    def test_the_channel_config_round_trips(self):
        import tomllib
        from psycho_carla.core.config import config_from_dict

        data = tomllib.loads(
            '[channels]\nenabled = true\ndefault_agent = "orchestrator"\n'
            '[channels.telegram]\ntoken = "abc"\n'
        )
        cfg = config_from_dict(data)
        assert cfg.channels.enabled is True
        assert cfg.channels.default_agent == "orchestrator"
        assert cfg.channels.channels["telegram"]["token"] == "abc"

    def test_the_channel_config_is_written_out(self):
        import tomllib
        from psycho_carla.core.config import config_from_dict, dump_toml

        cfg = config_from_dict({})
        cfg.channels.channels["telegram"] = {"token": "abc"}
        rendered = dump_toml(cfg)
        parsed = tomllib.loads(rendered)
        assert parsed["channels"]["telegram"]["token"] == "abc"

    def test_the_cli_exposes_the_channels_command(self):
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "psycho_carla", "--help"],
            capture_output=True, text=True, cwd=str(root),
            timeout=120,
        )
        assert result.returncode == 0
        assert "channels" in result.stdout

    def test_the_server_exposes_the_channel_routes(self):
        from psycho_carla.server.api.channels import router

        paths = {r.path for r in router.routes}
        assert "/channels" in paths
        assert "/channels/send" in paths
        assert "/channels/inbound/{channel}" in paths
        assert "/channels/status" in paths

class TestChannelParityFixes:
    """Round 7 — the gaps the gap-1 audit found and closed."""

    def test_upstream_channel_names_all_registered(self):
        """Every channel upstream registers is registered locally."""
        from psycho_carla.channels import available_channels
        upstream = {
            "bluebubbles", "discord", "email", "feishu", "gmail",
            "google_chat", "irc", "line", "mastodon", "matrix",
            "mattermost", "messenger", "nostr", "reddit", "rocketchat",
            "sendblue", "signal", "slack", "teams", "telegram", "twilio",
            "twitch", "twitter", "viber", "webchat", "webhook",
            "whatsapp", "whatsapp_baileys", "xmpp", "zulip",
        }
        local = set(available_channels())
        assert upstream - local == set()

    def test_canonical_upstream_keys_registered(self):
        """`messenger` and `twilio` are upstream's registry keys."""
        from psycho_carla.channels import available_channels
        local = set(available_channels())
        assert "messenger" in local
        assert "twilio" in local

    def test_every_registered_channel_can_be_constructed(self):
        """Every registered id builds without raising."""
        from psycho_carla.channels.registry import ChannelRegistry
        for channel_id, klass in ChannelRegistry.all().items():
            instance = klass({})
            assert instance.channel_id == channel_id

    def test_sendblue_credential_properties(self):
        from psycho_carla.channels.sendblue import SendBlueChannel
        channel = SendBlueChannel({
            "api_key_id": "key-1",
            "api_secret_key": "secret-1",
            "from_number": "+15551234567",
            "webhook_secret": "hook",
        })
        assert channel.api_key_id == "key-1"
        assert channel.api_secret_key == "secret-1"
        assert channel.from_number == "+15551234567"
        assert channel.webhook_secret == "hook"

    def test_sendblue_env_fallback(self):
        from psycho_carla.channels.sendblue import SendBlueChannel
        os.environ["SENDBLUE_API_KEY_ID"] = "env-key"
        os.environ["SENDBLUE_FROM_NUMBER"] = "+15550000000"
        try:
            channel = SendBlueChannel({})
            assert channel.api_key_id == "env-key"
            assert channel.from_number == "+15550000000"
        finally:
            os.environ.pop("SENDBLUE_API_KEY_ID", None)
            os.environ.pop("SENDBLUE_FROM_NUMBER", None)

    def test_sendblue_ignores_outbound_callbacks(self):
        from psycho_carla.channels.sendblue import SendBlueChannel
        channel = SendBlueChannel({"api_key_id": "k", "api_secret_key": "s",
                                   "from_number": "+1"})
        assert channel.handle_event(
            {"is_outbound": True, "content": "status update"}
        ) is None

    def test_sendblue_handle_webhook_delivers(self):
        from psycho_carla.channels.sendblue import SendBlueChannel
        channel = SendBlueChannel({"api_key_id": "k", "api_secret_key": "s",
                                   "from_number": "+1"})
        seen = []
        channel.on_message(seen.append)
        channel.handle_webhook({
            "from_number": "+15559876543",
            "content": "hello there",
            "message_handle": "h1",
        })
        assert len(seen) == 1
        assert seen[0].sender == "+15559876543"
        assert seen[0].content == "hello there"

    def test_twilio_handle_webhook_exists(self):
        from psycho_carla.channels.twilio_sms import TwilioSMSChannel
        assert callable(getattr(TwilioSMSChannel, "handle_webhook", None))

    def test_webchat_message_buffer(self):
        from psycho_carla.channels.webchat import WebChatChannel
        channel = WebChatChannel({})
        assert channel.get_messages() == []
        channel.send("webchat", "hello")
        assert len(channel.get_messages()) == 1
        assert channel.get_messages()[0].content == "hello"
        channel.clear_messages()
        assert channel.get_messages() == []

    def test_oauth1_header_is_well_formed(self):
        from psycho_carla.channels.httpclient import oauth1_header
        header = oauth1_header(
            "POST",
            "https://api.twitter.com/2/tweets",
            consumer_key="ck",
            consumer_secret="cs",
            access_token="at",
            access_secret="as",
        )
        assert header["Authorization"].startswith("OAuth ")
        assert "oauth_signature=" in header["Authorization"]
        assert "oauth_nonce=" in header["Authorization"]
        assert "oauth_signature_method=\"HMAC-SHA1\"" in header["Authorization"]

    def test_twitter_uses_oauth1_when_credentials_present(self):
        """The OAuth 1.0a path is taken when the full credential set exists."""
        import inspect
        from psycho_carla.channels.twitter import TwitterChannel
        source = inspect.getsource(TwitterChannel.send)
        assert "oauth1_header" in source
        assert "_oauth_credentials" in source

    def test_imessage_poll_new_messages_missing_db(self):
        from psycho_carla.channels.imessage_daemon import poll_new_messages
        assert poll_new_messages(
            db_path="/nonexistent/chat.db", last_rowid=0, chat_identifier="+1"
        ) == []

    def test_imessage_daemon_lifecycle(self):
        from psycho_carla.channels.imessage_daemon import (
            IMessageDaemonChannel, is_running, run_daemon, send_imessage,
            start_daemon, stop_daemon,
        )
        assert callable(run_daemon)
        assert callable(send_imessage)
        assert callable(start_daemon)
        assert is_running() is False
        assert stop_daemon() is False
        channel = IMessageDaemonChannel({"target": "+15551234567"})
        assert channel.is_running() is False
        assert channel.stop_daemon() is False

    def test_imessage_polls_a_real_database(self):
        """The poller reads inbound rows out of a Messages-shaped DB."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from psycho_carla.channels.imessage_daemon import poll_new_messages

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "chat.db"
            conn = sqlite3.connect(str(db))
            conn.executescript("""
                CREATE TABLE message (
                    ROWID INTEGER PRIMARY KEY, text TEXT, date REAL,
                    is_from_me INTEGER
                );
                CREATE TABLE chat (
                    ROWID INTEGER PRIMARY KEY, chat_identifier TEXT
                );
                CREATE TABLE chat_message_join (
                    message_id INTEGER, chat_id INTEGER
                );
                INSERT INTO chat VALUES (1, '+15551234567');
                INSERT INTO message VALUES (1, 'hello from boss', 1.0, 0);
                INSERT INTO message VALUES (2, 'my own reply', 2.0, 1);
                INSERT INTO chat_message_join VALUES (1, 1);
                INSERT INTO chat_message_join VALUES (2, 1);
            """)
            conn.commit()
            conn.close()

            rows = poll_new_messages(
                db_path=str(db), last_rowid=0, chat_identifier="+15551234567"
            )
            assert len(rows) == 1
            assert rows[0]["text"] == "hello from boss"

    def test_slack_daemon_module_api(self):
        from psycho_carla.channels import slack_daemon
        assert callable(slack_daemon.run_slack_daemon)
        assert callable(slack_daemon.start_slack_daemon)
        assert callable(slack_daemon.is_running)
        assert callable(slack_daemon.stop_daemon)
        assert slack_daemon.is_running() is False
        assert slack_daemon.stop_daemon() is False

    def test_slack_markdown_conversion(self):
        from psycho_carla.channels.slack_daemon import to_slack_fmt
        assert to_slack_fmt("# Title") == "*Title*"
        assert to_slack_fmt("**bold**") == "*bold*"
        assert to_slack_fmt("[text](https://x.com)") == "<https://x.com|text>"
        assert to_slack_fmt("~~gone~~") == "~gone~"

    def test_bridge_more_command(self):
        from psycho_carla.channels.bridge import ChannelBridge
        from psycho_carla.channels.base import ChannelMessage
        from psycho_carla.sessions.session import SessionStore
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "s.db")
            try:
                bridge = ChannelBridge({}, session_store=store)
                message = ChannelMessage(
                    channel="webchat", sender="alice", content="/more"
                )
                assert "No pending response" in bridge.handle_incoming(message)

                session_id = bridge._session_for(message)
                store.set_pending_response(session_id, "the long answer")
                reply = bridge.handle_incoming(message)
                assert "the long answer" in reply
                # Collected once — a second /more finds nothing.
                assert "No pending response" in bridge.handle_incoming(message)
            finally:
                store.close()

    def test_bridge_notify_command(self):
        from psycho_carla.channels.bridge import ChannelBridge
        from psycho_carla.channels.base import ChannelMessage
        from psycho_carla.sessions.session import SessionStore
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "s.db")
            try:
                bridge = ChannelBridge({}, session_store=store)
                message = ChannelMessage(
                    channel="webchat", sender="alice", content="/notify slack"
                )
                reply = bridge.handle_incoming(message)
                assert "slack" in reply
                session_id = bridge._session_for(message)
                assert store.get_notification_preference(session_id) == "slack"
            finally:
                store.close()

    def test_bridge_sessions_command(self):
        from psycho_carla.channels.bridge import ChannelBridge
        from psycho_carla.channels.base import ChannelMessage
        from psycho_carla.sessions.session import SessionStore
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "s.db")
            try:
                bridge = ChannelBridge({}, session_store=store)
                message = ChannelMessage(
                    channel="webchat", sender="alice", content="/sessions"
                )
                reply = bridge.handle_incoming(message)
                assert "Conversations" in reply or "No conversations" in reply
            finally:
                store.close()

    def test_session_notification_methods(self):
        from psycho_carla.sessions.session import SessionStore
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "s.db")
            try:
                store.create("s1", metadata={"user_id": "alice"})
                assert store.set_notification_preference("s1", "telegram")
                assert store.get_notification_preference("s1") == "telegram"
                targets = store.get_notification_targets()
                assert any(t["preferred_notification_channel"] == "telegram"
                           for t in targets)

                assert store.set_pending_response("s1", "hello")
                assert store.get_pending_response("s1") == "hello"
                assert store.clear_pending_response("s1")
                assert store.get_pending_response("s1") == ""
            finally:
                store.close()

    def test_session_schema_migrates_an_old_database(self):
        """A database created before the new columns still opens."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from psycho_carla.sessions.session import SessionStore

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "old.db"
            conn = sqlite3.connect(str(db))
            conn.execute("""
                CREATE TABLE sessions (
                    session_id TEXT PRIMARY KEY, user_id TEXT,
                    display_name TEXT DEFAULT '',
                    channel_ids TEXT DEFAULT '{}',
                    created_at REAL, last_activity REAL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.commit()
            conn.close()

            store = SessionStore(db)
            try:
                store.create("s1", metadata={"user_id": "bob"})
                assert store.set_notification_preference("s1", "slack")
                assert store.get_notification_preference("s1") == "slack"
            finally:
                store.close()

    def test_bridge_notify_pushes_to_channels(self):
        """A notification reaches the channel the session chose."""
        from psycho_carla.channels.base import BaseChannel, ChannelStatus
        from psycho_carla.channels.bridge import ChannelBridge
        from psycho_carla.sessions.session import SessionStore
        import tempfile
        from pathlib import Path

        class FakeChannel(BaseChannel):
            channel_id = "fake"
            requires_auth = False

            def __init__(self):
                super().__init__({})
                self.sent = []

            def connect(self):
                self.set_status(ChannelStatus.CONNECTED)

            def disconnect(self):
                self.set_status(ChannelStatus.DISCONNECTED)

            def send(self, channel, content, *, conversation_id="",
                     metadata=None):
                self.sent.append((channel, content))
                return True

            def status(self):
                return self._status

            def list_channels(self):
                return ["fake"]

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(Path(tmp) / "s.db")
            try:
                fake = FakeChannel()
                bridge = ChannelBridge({"fake": fake}, session_store=store)
                store.create("s1", metadata={"user_id": "alice"})
                store.set_notification_preference("s1", "fake")

                delivered = bridge.notify("agent_done", "Agent finished")
                assert delivered == 1
                assert fake.sent[0][1] == "Agent finished"

                # Rate limited: the same event type does not fire twice.
                assert bridge.notify("agent_done", "Agent finished") == 0
                # A different event type does.
                assert bridge.notify("agent_error", "Agent failed") == 1
            finally:
                store.close()

class TestNoUndefinedNames:
    """A latent NameError is a crash waiting for the right input.

    These modules are only exercised on the code path that needs them,
    so an undefined name can sit undetected for months. pyflakes catches
    them without running anything.
    """

    def test_no_undefined_names_in_the_package(self):
        pyflakes = pytest.importorskip("pyflakes")
        import pyflakes.api
        import pyflakes.reporter

        import psycho_carla

        root = pathlib.Path(psycho_carla.__file__).parent
        warnings = []

        class Collector(pyflakes.reporter.Reporter):
            def unexpectedError(self, filename, msg):  # noqa: N802
                warnings.append(f"{filename}: {msg}")

            def syntaxError(self, filename, msg, *args, **kwargs):  # noqa: N802
                warnings.append(f"{filename}: {msg}")

            def flake(self, message):
                if isinstance(message, pyflakes.messages.UndefinedName):
                    warnings.append(
                        f"{message.filename}: undefined name "
                        f"'{message.message_args[0]}'"
                    )

        collector = Collector(sys.stdout, sys.stdout)
        check = pyflakes.api.checkRecursive([str(root)], collector)

        # Unused imports are style, not bugs. Only an undefined name
        # can crash at runtime, so that is what this test guards.
        assert not warnings, "undefined names found:\n" + "\n".join(warnings)

class TestNativeEngine:
    """The in-process CPU engine — C.A.R.L.A. running a model itself."""

    def test_the_adapter_is_registered(self):
        from psycho_carla.providers.base import available_adapters
        assert "native_onnx" in available_adapters()

    def test_the_adapter_reports_itself_unconfigured_without_a_model(self):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        adapter = NativeOnnxAdapter({})
        ok, detail = adapter.health()
        assert not ok
        assert "model_path" in detail

    def test_health_reports_the_graph_it_would_load(self, tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        (tmp_path / "model_q4.onnx").write_bytes(b"not really a model")
        adapter = NativeOnnxAdapter({"model_path": str(tmp_path)})
        ok, detail = adapter.health()
        assert ok
        assert "model_q4.onnx" in detail

    def test_graph_selection_prefers_the_smallest_fitting_graph(self, tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_q4.onnx").write_bytes(b"x" * 4096)
        (onnx_dir / "model_int8.onnx").write_bytes(b"x" * 8192)
        graph = NativeOnnxAdapter._find_graph(tmp_path)
        assert graph.name == "model_q4.onnx"

    def test_graph_selection_falls_back_to_the_smallest(self, tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_int8.onnx").write_bytes(b"x" * 8192)
        (onnx_dir / "model.onnx").write_bytes(b"x" * 16384)
        graph = NativeOnnxAdapter._find_graph(tmp_path)
        assert graph.name == "model_int8.onnx"

    def test_missing_graph_raises_a_clear_error(self, tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        adapter = NativeOnnxAdapter({"model_path": str(tmp_path)})
        with pytest.raises(FileNotFoundError):
            adapter._resolve_paths()

    def test_oauth1_header_signs_a_request(self):
        from psycho_carla.channels.httpclient import oauth1_header
        header = oauth1_header(
            "POST",
            "https://api.twitter.com/2/tweets",
            consumer_key="ck",
            consumer_secret="cs",
            access_token="at",
            access_secret="as",
        )
        auth = header["Authorization"]
        assert auth.startswith("OAuth ")
        for field in ("oauth_consumer_key", "oauth_nonce",
                      "oauth_signature_method", "oauth_signature",
                      "oauth_timestamp", "oauth_token", "oauth_version"):
            assert field in auth

    def test_oauth1_header_is_deterministic_in_shape(self):
        """Two signatures differ (nonce and timestamp move) but both parse."""
        from psycho_carla.channels.httpclient import oauth1_header
        import re
        for _ in range(2):
            header = oauth1_header(
                "POST", "https://example.com/x",
                consumer_key="k", consumer_secret="s",
                access_token="t", access_secret="a",
            )
            auth = header["Authorization"]
            pairs = dict(
                (m.group(1).strip(), m.group(2))
                for m in re.finditer(r'([^=,]+?)="([^"]*)"', auth)
            )
            assert "oauth_signature" in pairs
            assert pairs["oauth_signature_method"] == "HMAC-SHA1"

    def test_the_native_catalog_lists_models(self):
        from psycho_carla.intelligence.model_catalog import (
            list_models, find_model_spec,
        )
        native = list_models(engine="native_onnx")
        assert native
        for spec in native:
            assert spec.onnx_repo
            assert spec.onnx_file.endswith(".onnx")
            assert spec.ram_gb > 0

    def test_native_specs_resolve_for_the_native_engine(self):
        from psycho_carla.intelligence.model_catalog import (
            find_model_spec, resolve_model_id_for_engine,
        )
        spec = find_model_spec("native:smollm2-360m")
        assert spec is not None
        assert resolve_model_id_for_engine(
            "native:smollm2-360m", "native_onnx"
        ) == spec.onnx_repo

    def test_the_catalog_includes_the_native_models(self):
        from psycho_carla.intelligence.model_catalog import all_specs
        ids = {s.model_id for s in all_specs()}
        assert any(i.startswith("native:") for i in ids)

    def test_pull_rejects_a_model_with_no_onnx_export(self):
        from psycho_carla.intelligence.model_manager import pull_model
        result = pull_model("qwen3:0.6b", engine="native_onnx")
        assert not result.ok
        assert "onnx" in result.message.lower()

    def test_pull_rejects_an_unknown_native_model(self):
        from psycho_carla.intelligence.model_manager import pull_model
        result = pull_model("native:not-a-real-model", engine="native_onnx")
        assert not result.ok
        assert "catalog" in result.message.lower()

    def test_native_models_are_listed_and_deleted(self, tmp_path, monkeypatch):
        from psycho_carla.intelligence import model_manager

        monkeypatch.setattr(model_manager, "models_dir", lambda: tmp_path)
        directory = model_manager.native_model_dir("native:test")
        (directory / "onnx").mkdir(parents=True)
        (directory / "onnx" / "model_q4.onnx").write_bytes(b"x" * 8192)

        found = model_manager.native_models()
        assert any(m["id"] == "native_test" for m in found)
        assert all(m["engine"] == "native_onnx" for m in found)

        result = model_manager.delete_native_model("native_test")
        assert result.ok
        assert model_manager.native_models() == []

    def test_deleting_a_missing_native_model_is_reported(self):
        from psycho_carla.intelligence.model_manager import (
            delete_native_model,
        )
        result = delete_native_model("definitely-not-here")
        assert not result.ok
        assert "no native model" in result.message

    def test_activating_a_missing_native_model_fails_cleanly(self):
        from psycho_carla.intelligence.model_manager import (
            activate_native_model,
        )
        result = activate_native_model("native:not-downloaded")
        assert not result["ok"]
        assert "not downloaded" in result["error"]

    def test_the_local_inventory_includes_native_models(self, tmp_path,
                                                       monkeypatch):
        from psycho_carla.intelligence import model_manager

        monkeypatch.setattr(model_manager, "models_dir", lambda: tmp_path)
        directory = model_manager.native_model_dir("native:test")
        (directory / "onnx").mkdir(parents=True)
        (directory / "onnx" / "model_q4.onnx").write_bytes(b"x" * 8192)

        engines = {m["engine"] for m in model_manager.local_models()}
        assert "native_onnx" in engines

    def test_the_sampler_is_greedy_at_zero_temperature(self):
        import numpy as np
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        adapter = NativeOnnxAdapter({})
        logits = np.array([[[0.1, 5.0, 0.2]]], dtype=np.float32)
        token = adapter._sampler(logits, 0.0, 1.0, 0, np.random.default_rng(0))
        assert token == 1

    def test_the_repetition_penalty_damps_a_seen_token(self):
        import numpy as np
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        adapter = NativeOnnxAdapter({})
        logits = np.array([[[4.0, 3.0]]], dtype=np.float32)
        plain = adapter._sampler(
            logits, 0.0, 1.0, 0, np.random.default_rng(0), 1.0, []
        )
        penalised = adapter._sampler(
            logits, 0.0, 1.0, 0, np.random.default_rng(0), 2.0, [0]
        )
        assert plain == 0
        # With token 0 damped, token 1 now wins.
        assert penalised == 1

    def test_the_tokenizer_directory_is_found_above_the_graph(self, tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        root = tmp_path / "model"
        onnx_dir = root / "onnx"
        onnx_dir.mkdir(parents=True)
        (onnx_dir / "model_q4.onnx").write_bytes(b"x")
        (root / "tokenizer.json").write_text("{}")
        found = NativeOnnxAdapter._find_tokenizer_dir(
            onnx_dir / "model_q4.onnx"
        )
        assert found == root

    def test_the_tokenizer_directory_falls_back_to_the_graph_folder(self,
                                                                   tmp_path):
        from psycho_carla.providers.adapters.native_onnx import (
            NativeOnnxAdapter,
        )
        root = tmp_path / "model"
        root.mkdir()
        (root / "model_q4.onnx").write_bytes(b"x")
        found = NativeOnnxAdapter._find_tokenizer_dir(root / "model_q4.onnx")
        assert found == root

    def test_the_optimisation_level_respects_available_memory(self):
        from psycho_carla.providers.adapters.native_onnx import (
            _optimization_level, _free_ram_gb, _available_ram_gb,
        )
        assert _optimization_level() is not None
        assert _free_ram_gb() > 0
        assert _available_ram_gb() > 0

    def test_the_cli_exposes_the_native_subcommands(self):
        from psycho_carla.cli.main import build_parser
        parser = build_parser()
        args = parser.parse_args(["model", "native", "native:smollm2-360m"])
        assert args.model_cmd == "native"
        args = parser.parse_args(
            ["model", "activate", "native:smollm2-360m", "--tier", "fast_tier"]
        )
        assert args.model_cmd == "activate"
        assert args.tier == "fast_tier"

    def test_the_api_exposes_activate_and_delete(self):
        from psycho_carla.server.app import create_app
        paths = create_app().openapi()["paths"]
        assert "/api/models/activate" in paths
        assert "/api/models/delete" in paths

