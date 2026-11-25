"""
PSYCHO-C.A.R.L.A. — Command Line Interface.

    carla init          create a starter psycho_carla.toml
    carla doctor        diagnose the environment
    carla ask "..."     one-shot query
    carla chat          interactive REPL
    carla serve         start the HUD + OpenAI-compatible API
    carla agents        list agents
    carla tiers         list capability tiers
    carla memory ...    memory operations
    carla cron ...      scheduler operations
    carla optimize      run the learning optimizer
    carla bench         run a performance benchmark
    carla eval          run a quality evaluation suite
    carla auth ...      server API-key management
    carla voice ...     voice commands
    carla mcp           MCP server / client commands

Layer: Core.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

from ..core.config import (
    CarlaConfig,
    default_config_path,
    load_config,
    save_config,
)
from ..core.persona import Persona
from ..core.runtime import CarlaRuntime
from ..core.telemetry import hardware_summary

BANNER = r"""
   ___                       _____ ____  _     _____
  / _ \ _ __  ___ _   _ _   |  ___|  _ \| |   |_   _| ___  _ __ __ _  ___
 | | | | '_ \/ -_) ' \ | | | |_   | | | | |    | | |/ _ \/ '__/ _` |/ _ \
 | |_| | |_) \___|\_||_||_| |___| |_| |_|_| |_| |_|_|\__\_\| |_| \__,_|\__/
  \___/| .__/
       |_|                P S Y C H O - F . R . I . D . A . Y .
"""


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _runtime(args: argparse.Namespace, with_voice: bool = True) -> CarlaRuntime:
    cfg = load_config(getattr(args, "config", None))
    return CarlaRuntime(cfg, with_voice=with_voice)


def _c(text: str, code: str) -> str:
    colors = {
        "cyan": "\033[38;5;51m", "blue": "\033[38;5;39m",
        "dim": "\033[2m", "bold": "\033[1m", "green": "\033[38;5;46m",
        "red": "\033[38;5;203m", "amber": "\033[38;5;214m", "end": "\033[0m",
    }
    return f"{colors.get(code, '')}{text}{colors['end']}"


def _print_banner() -> None:
    if sys.stdout.isatty():
        print(_c(BANNER, "cyan"))
    else:
        print("PSYCHO-C.A.R.L.A.")


def _persona_line(cfg: CarlaConfig, text: str) -> None:
    """Print a line the way C.A.R.L.A. would say it."""
    persona = Persona(cfg.persona)
    channel = persona.split(text)
    if channel.speech:
        print(_c("🔊 " + channel.speech, "cyan"))
    if channel.display and channel.display != channel.speech:
        print()
        print(channel.display)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_init(args: argparse.Namespace) -> int:
    from ..config_template import render_default_config
    from ..templates.presets import (
        available_presets,
        install_preset,
        preset_description,
    )

    path = Path(args.config) if args.config else default_config_path()

    if getattr(args, "list_presets", False):
        print(_c("\n  Presets\n", "cyan"))
        for name in available_presets():
            print(f"  {_c(name, 'cyan'):<26} {preset_description(name)}")
        print(_c("\n  carla init --preset <name>\n", "dim"))
        return 0

    if getattr(args, "preset", ""):
        name = args.preset
        try:
            install_preset(name, path, force=args.force)
        except FileNotFoundError:
            print(_c(f"\n  Unknown preset '{name}'.", "amber"))
            print(_c("  Run: carla init --list-presets\n", "dim"))
            return 1
        except FileExistsError as exc:
            print(_c(f"\n  {exc}\n", "amber"))
            return 1
        print(_c(f"✓ Wrote {path}  (preset: {name})", "green"))
        print(_c("Next: carla doctor", "dim"))
        return 0

    if path.exists() and not args.force:
        print(_c(f"Config already exists at {path}", "amber"))
        print(_c("Use --force to overwrite.", "dim"))
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_default_config(), encoding="utf-8")
    print(_c(f"✓ Wrote {path}", "green"))
    print(_c("Next: carla doctor", "dim"))
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    """List and show the bundled agent templates."""
    from ..templates.agent_templates import (
        available_templates,
        load_template,
        load_template_dir,
    )

    if args.templates_cmd == "list":
        templates = load_template_dir()
        print(_c("\n  Agent templates\n", "cyan"))
        for name, template in sorted(templates.items()):
            print(f"  {_c(name, 'cyan'):<22} {template.description}")
        print(_c(f"\n  {len(templates)} templates "
                 f"(carla templates show <name>)\n", "dim"))
        return 0

    if args.templates_cmd == "show":
        templates = load_template_dir()
        template = templates.get(args.name)
        if template is None:
            # Fall back to a file on disk.
            try:
                template = load_template(args.name)
            except Exception:
                print(_c(f"\n  No template named '{args.name}'.\n", "amber"))
                print(_c(f"  Available: {', '.join(available_templates())}\n", "dim"))
                return 1
        print(_c(f"\n  {template.name}\n", "cyan"))
        print(f"    {template.description}")
        print(f"    type        {template.agent_type}")
        print(f"    max_turns   {template.max_turns}")
        print(f"    temperature {template.temperature}")
        print(f"    tools       {', '.join(template.tools) or '(none)'}")
        print(_c("\n  System prompt\n", "bold"))
        for line in template.system_prompt.splitlines():
            print(f"    {line}")
        print()
        return 0
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    hw = hardware_summary()

    print(_c("\n══ PSYCHO-C.A.R.L.A. — System Diagnosis ══\n", "cyan"))
    print(_c(f"  Config path : {cfg.config_path}", "dim"))
    print(_c(f"  State dir   : {cfg.home}", "dim"))
    print(_c(f"  Platform    : {hw['platform']}", "dim"))
    print(_c(f"  Python      : {hw['python']}", "dim"))
    print(_c(f"  CPU cores   : {hw['cpu_count']}", "dim"))
    print(_c(f"  RAM         : {hw['ram_used_mb']/1024:.1f} / {hw['ram_total_mb']/1024:.1f} GB", "dim"))
    if hw["vram_total_mb"]:
        print(_c(f"  VRAM        : {hw['vram_used_mb']:.0f} / {hw['vram_total_mb']:.0f} MB", "dim"))
    else:
        print(_c("  VRAM        : none detected (CPU-only mode)", "dim"))

    # -- tiers -------------------------------------------------------------
    print(_c("\n  Capability tiers", "bold"))
    if not cfg.intelligence.tiers:
        print(_c("    ✗ No tiers configured. Run: carla init --force", "red"))
    else:
        for alias, tier in cfg.intelligence.tiers.items():
            mark = "▸" if alias == cfg.intelligence.default_tier else " "
            print(_c(f"    {mark} {alias:<16}", "cyan"),
                  f"{tier.provider:<14} {tier.model or '(unset)'}")

    # -- providers ---------------------------------------------------------
    print(_c("\n  Provider connectivity", "bold"))
    rt = None
    try:
        rt = CarlaRuntime(cfg, with_voice=False, with_mcp=False)
        results = rt.router.health()
        for r in results:
            ok = r["ok"]
            icon = _c("✓", "green") if ok else _c("✗", "red")
            print(f"    {icon} {r['provider']:<16} {r['detail']}")
        if not results:
            print(_c("    (none configured)", "dim"))
    except Exception as exc:
        print(_c(f"    ✗ Runtime init failed: {exc}", "red"))

    # -- optional dependencies ---------------------------------------------
    print(_c("\n  Optional components", "bold"))
    for label, module, purpose in [
        ("fastapi", "fastapi", "HUD web server"),
        ("uvicorn", "uvicorn", "ASGI server"),
        ("httpx", "httpx", "HTTP client"),
        ("psutil", "psutil", "resource telemetry"),
        ("kokoro", "kokoro", "CPU TTS (Irish female)"),
        ("piper-tts", "piper", "CPU TTS (fallback)"),
        ("faster-whisper", "faster_whisper", "CPU STT"),
        ("sounddevice", "sounddevice", "microphone capture"),
        ("openwakeword", "openwakeword", "wake word"),
        ("numpy", "numpy", "audio maths"),
    ]:
        try:
            __import__(module)
            print(f"    {_c('✓', 'green')} {label:<16} {purpose}")
        except Exception:
            print(f"    {_c('○', 'dim')} {label:<16} {purpose} {_c('(not installed)', 'dim')}")

    # -- storage ------------------------------------------------------------
    print(_c("\n  Storage", "bold"))
    for label, path in [
        ("Memory DB", cfg.memory_db),
        ("Telemetry", cfg.telemetry_db),
        ("Traces", cfg.traces_db),
    ]:
        exists = path.exists()
        size = f"{path.stat().st_size/1024:.1f} KB" if exists else "not created"
        print(f"    {label:<16} {path} {_c('(' + size + ')', 'dim')}")

    if rt:
        rt.stop()
    print(_c("\n  ══ Ready ══\n", "cyan"))
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=not args.no_voice)
    rt.start()
    try:
        result = rt.ask(
            args.prompt,
            agent=args.agent,
            tier=args.tier or "",
            session_id=args.session or "cli",
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
            return 0
        _persona_line(rt.config, result.get("display") or result.get("text", ""))
        if result.get("tool_calls"):
            print()
            print(_c("Tools: " + ", ".join(
                f"{t['name']}{'' if t.get('ok') else '!'}" for t in result["tool_calls"]
            ), "dim"))
        if rt.voice and result.get("speech") and not args.no_voice:
            rt.voice.speak(result["speech"])
        return 0
    finally:
        rt.stop()


def cmd_chat(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=not args.no_voice)
    rt.start()
    persona = Persona(rt.config.persona)
    print(_c(BANNER, "cyan"))
    print(_c(f"  {persona.salutation('greeting')}\n", "cyan"))
    print(_c("  Type 'exit' to leave, 'tier <name>' to switch, 'agents' to list.\n", "dim"))

    try:
        while True:
            try:
                user = input(_c(f"{persona.boss}: ", "blue")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not user:
                continue
            low = user.lower()
            if low in ("exit", "quit", "bye"):
                print(_c(persona.salutation("neutral"), "cyan"))
                break
            if low == "agents":
                for a in rt.list_agents():
                    print(f"  {_c(a['name'], 'cyan'):<22} {a['description']}")
                continue
            if low.startswith("tier "):
                alias = user.split(" ", 1)[1].strip()
                if rt.router.set_active_tier(alias):
                    print(_c(f"Tier → {alias}", "green"))
                else:
                    print(_c(f"Unknown tier '{alias}'.", "red"))
                continue

            result = rt.ask(user, agent=args.agent, tier=args.tier or "",
                            session_id=args.session or "cli")
            print()
            print(result.get("display") or result.get("text", ""))
            if rt.voice and result.get("speech") and not args.no_voice:
                rt.voice.speak(result["speech"])
            print()
    finally:
        rt.stop()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    cfg = load_config(args.config)
    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    print(_c(f"\n  PSYCHO-C.A.R.L.A. console → http://{host}:{port}\n", "cyan"))
    print(_c(f"  OpenAI-compatible API → http://{host}:{port}{cfg.server.openai_compat_prefix}\n", "dim"))
    uvicorn.run(
        "psycho_carla.server.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=args.log_level.lower(),
        reload=args.reload,
    )
    return 0


def cmd_agents(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        print(_c("\n  Built-in agents\n", "cyan"))
        for a in rt.list_agents():
            print(f"  {_c('▸', 'cyan')} {a['name']:<20} {_c(a['mode'], 'blue')}")
            print(f"    {a['description']}")
            if a.get("tier"):
                print(f"    tier: {a['tier']}   tools: {', '.join(a.get('tools', [])[:6])}")
            print()
    finally:
        rt.stop()
    return 0


def cmd_tiers(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        print(_c("\n  Capability tiers\n", "cyan"))
        for t in rt.router.list_tiers():
            mark = _c("▸", "green") if t["active"] else " "
            print(f"  {mark} {_c(t['alias'], 'cyan'):<18} {t['provider']:<14} {t['model']}")
            if t.get("description"):
                print(f"      {t['description']}")
        print()
    finally:
        rt.stop()
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        if args.memory_cmd == "search":
            results = rt.memory.search(args.query, top_k=args.limit)
            for m in results:
                print(_c(f"  [{m.id}] ", "dim"), f"{m.content}  {_c(f'({m.score:.2f})', 'dim')}")
            if not results:
                print(_c(f"  No matches, {rt.persona.boss}.", "dim"))
        elif args.memory_cmd == "list":
            for m in rt.memory.list_memories(limit=args.limit):
                print(_c(f"  [{m.id}] ", "dim"), m.content)
        elif args.memory_cmd == "store":
            mem_id = rt.memory_store(args.text)
            print(_c(f"  Stored memory {mem_id}.", "green"))
        elif args.memory_cmd == "delete":
            ok = rt.memory_delete(args.memory_id)
            print(_c("  Deleted." if ok else "  Not found.", "green" if ok else "red"))
        elif args.memory_cmd == "clear":
            n = rt.memory.clear_memories()
            print(_c(f"  Purged {n} memories.", "green"))
        elif args.memory_cmd == "sessions":
            for s in rt.memory.list_sessions(limit=args.limit):
                print(_c(f"  {s['session_id']}", "cyan"), _c(f"  {s['updated_at']}", "dim"))
        elif args.memory_cmd == "stats":
            print(json.dumps(rt.memory.stats(), indent=2))
    finally:
        rt.stop()
    return 0


def cmd_cron(args: argparse.Namespace) -> int:
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        if args.cron_cmd == "list":
            jobs = rt.list_jobs()
            if not jobs:
                print(_c(f"  No jobs scheduled, {rt.persona.boss}.", "dim"))
            for j in jobs:
                print(f"  {_c(j['id'], 'dim')}  {_c(j['schedule'], 'cyan'):<16} "
                      f"{j['name']:<22} {j['agent']}  {_c(j['last_status'] or '—', 'dim')}")
        elif args.cron_cmd == "add":
            job = rt.add_job(
                name=args.name, schedule=args.schedule, agent=args.agent,
                prompt=args.prompt or f"Run the {args.name} scheduled task.",
            )
            print(_c(f"  Added job {job.id}.", "green"))
        elif args.cron_cmd == "remove":
            ok = rt.remove_job(args.job_id)
            print(_c("  Removed." if ok else "  Not found.", "green" if ok else "red"))
        elif args.cron_cmd == "run":
            ok = rt.scheduler.run_now(args.job_id)
            print(_c("  Dispatched." if ok else "  Not found.", "green" if ok else "red"))
    finally:
        rt.stop()
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    """Run the learning optimiser over one or all four surfaces."""
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        learning = rt.config.learning

        # -- routing -------------------------------------------------------- #
        if args.surface in ("", "routing"):
            policies = rt.optimizer.optimize_routing(min_samples=args.min_samples)
            if policies:
                print(_c("\n  Routing recommendations\n", "cyan"))
                for name, p in policies.items():
                    print(f"  {_c(p.agent, 'cyan'):<20} → {p.recommended_tier}")
                    print(f"    {p.rationale}")
            elif args.surface:
                print(_c("\n  No routing policy — not enough traces yet.\n", "dim"))

        # -- prompt / intelligence ------------------------------------------ #
        if args.surface in ("", "prompt", "intelligence"):
            if args.agent:
                suggestion = rt.optimizer.optimize_prompt(args.agent)
                if suggestion:
                    print(_c(f"\n  Prompt suggestion for {args.agent}\n", "cyan"))
                    for r in suggestion.rationale:
                        print(_c(f"    • {r}", "dim"))
                    print()
                    print(textwrap.indent(suggestion.proposed_prompt, "    "))
                    print(_c(f"\n  confidence: {suggestion.confidence}", "dim"))
                advice = rt.optimizer.advise(args.agent)
                if advice["advice"]:
                    print(_c("\n  Advisor\n", "cyan"))
                    for a in advice["advice"]:
                        print(_c(f"    • {a}", "dim"))

        # -- tool selection -------------------------------------------------- #
        if args.surface in ("", "tools", "tool_selection"):
            policies = rt.tool_optimizer.optimize_tool_selection(agent=args.agent)
            if policies:
                print(_c("\n  Tool selection\n", "cyan"))
                for name, p in policies.items():
                    print(f"  {_c(p.agent, 'cyan'):<20} ({p.sample_count} tool uses)")
                    if p.keep:
                        print(f"    keep   : {', '.join(p.keep)}")
                    if p.promote:
                        print(f"    promote: {', '.join(p.promote)}")
                    if p.drop:
                        print(_c(f"    drop   : {', '.join(p.drop)}", "amber"))
                    print(f"    confidence: {p.confidence}")
            elif args.surface:
                print(_c("\n  No tool policy — not enough traces yet.\n", "dim"))

        # -- engine specification --------------------------------------------- #
        if args.surface in ("", "engine", "engine_spec"):
            specs = rt.engine_optimizer.optimize_engine_spec(tier=args.tier)
            if specs:
                print(_c("\n  Engine specification\n", "cyan"))
                for name, s in specs.items():
                    print(f"  {_c(name, 'cyan'):<20} → {s.model or 'unknown'}")
                    print(f"    quantization   : {s.quantization}")
                    print(f"    context_window : {s.context_window}")
                    print(f"    max_tokens     : {s.max_tokens}")
                    print(f"    temperature    : {s.temperature}")
                    print(f"    confidence     : {s.confidence}")
                    for r in s.rationale:
                        print(_c(f"    • {r}", "dim"))
            elif args.surface:
                print(_c("\n  No engine spec — not enough traces yet.\n", "dim"))

        # -- skills catalogue ---------------------------------------------------- #
        if args.surface in ("", "skills"):
            from ..learning.skills_optimizer import SkillOptimizer
            from ..skills import SkillStore

            store = SkillStore(rt.config.skills_dir)
            report = SkillOptimizer(store.catalog).optimize(policy=args.policy)
            print(_c("\n  Skills catalogue\n", "cyan"))
            if not report.scanned:
                print(_c("    No skills installed.\n", "dim"))
            else:
                print(f"    scanned        : {report.scanned}")
                print(f"    policy         : {report.policy}")
                if report.issues:
                    print(_c("\n  Issues\n", "amber"))
                    for issue in report.issues:
                        colour = {"error": "amber", "warning": "amber",
                                  "info": "dim"}.get(issue.severity, "dim")
                        print(_c(f"    {issue.skill}: {issue.message}", colour))
                        if issue.suggestion:
                            print(_c(f"      → {issue.suggestion}", "dim"))
                if report.ranking:
                    print(_c("\n  Ranking (worst first)\n", "cyan"))
                    for name in report.ranking:
                        print(f"    {name:<28} {report.scores[name]:.2f}")
            print()

        # -- surface policy state ---------------------------------------------- #
        if not args.surface:
            print(_c("\n  Learning surfaces\n", "cyan"))
            for surface in ("routing", "intelligence", "agent", "tools", "engine"):
                policy = learning.policy_for(surface)
                state = policy if policy else _c("disabled", "dim")
                print(f"    {surface:<16} {state}")
            print()
    finally:
        rt.stop()
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """Run a performance benchmark against the agent stack."""
    from ..bench import Bench, BenchWorkload

    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        if args.workload_file:
            data = json.loads(Path(args.workload_file).read_text(encoding="utf-8"))
            workload = BenchWorkload.from_dict(data)
        elif args.workload == "tool_use":
            workload = BenchWorkload.tool_use(turns=args.turns)
        else:
            workload = BenchWorkload.chat(turns=args.turns, tier=args.tier)

        print(_c(f"\n  Running workload '{workload.name}' "
                 f"({len(workload.cases)} cases)\n", "cyan"))
        report = Bench(rt).run(
            workload, tier=args.tier, repeat=args.repeat, verbose=args.verbose
        )
        summary = report.summary()

        print(_c("\n  Results\n", "cyan"))
        print(f"    cases        : {summary['cases']}")
        print(f"    passed       : {summary['passed']} "
              f"({summary['success_rate']}%)")
        print(f"    duration     : {summary['duration_s']} s")
        lat = summary["latency_ms"]
        print(f"    latency mean : {lat['mean']} ms")
        print(f"    latency p95  : {lat['p95']} ms")
        print(f"    latency max  : {lat['max']} ms")
        tps = summary["tokens_per_second"]
        if tps["mean"] is not None:
            print(f"    tokens/sec   : {tps['mean']}")
        print(f"    total tokens : {summary['total_tokens']}")
        print(f"    energy       : {summary['energy']['total_joules']} J "
              f"({summary['energy']['joules_per_case']} J/case)")
        print(f"    flops        : {summary['total_flops']:.3e}")
        if summary["errors"]:
            print(_c("\n  Errors\n", "amber"))
            for err in summary["errors"]:
                print(_c(f"    {err['name']}: {err['error']}", "amber"))

        if args.json:
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report.to_json(), encoding="utf-8")
            print(_c(f"\n  Report written to {out}\n", "green"))
        else:
            print()
    finally:
        rt.stop()
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    """Run a quality evaluation suite against the agent stack."""
    from ..bench import EvalReport, EvalSuite, get_suite, suite_names

    if getattr(args, "list_suites", False):
        from ..bench import BUILTIN_SUITES

        print(_c("\n  Built-in eval suites\n", "cyan"))
        for name in suite_names():
            print(f"    {name:<12} {len(BUILTIN_SUITES[name].cases)} cases")
        print()
        return 0

    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        if args.eval_file:
            suite = EvalSuite.from_json(args.eval_file)
        elif getattr(args, "suite", ""):
            suite = get_suite(args.suite)
            if suite is None:
                print(_c(f"\n  Unknown suite '{args.suite}'. "
                         f"Available: {', '.join(suite_names())}\n", "amber"))
                return 2
        else:
            suite = _default_eval_suite()

        print(_c(f"\n  Running eval suite '{suite.name}' "
                 f"({len(suite.cases)} cases)\n", "cyan"))
        report = suite.run(rt, verbose=args.verbose)
        summary = report.summary()

        print(_c("\n  Results\n", "cyan"))
        print(f"    cases      : {summary['cases']}")
        print(f"    passed     : {summary['passed']}")
        print(f"    failed     : {summary['failed']}")
        print(f"    accuracy   : {summary['accuracy']}%")
        print(f"    duration   : {summary['duration_s']} s")
        print(f"    avg latency: {summary['avg_latency_ms']} ms")
        print(f"    total tokens: {summary['total_tokens']}")
        if summary["failures"]:
            print(_c("\n  Failures\n", "amber"))
            for fail in summary["failures"]:
                print(_c(f"    {fail['name']}", "amber"))
                for reason in fail.get("failures", []):
                    print(_c(f"      • {reason}", "dim"))
                if fail.get("error"):
                    print(_c(f"      error: {fail['error']}", "dim"))

        if getattr(args, "compare", ""):
            from ..bench import compare_reports

            baseline_data = json.loads(
                Path(args.compare).read_text(encoding="utf-8"))
            baseline = EvalReport(
                suite=baseline_data.get("suite", "baseline"),
                results=[],
            )
            from ..bench import EvalResult as _ER

            baseline.results = [
                _ER(name=r.get("name", ""), passed=bool(r.get("passed")),
                    failures=list(r.get("failures", [])),
                    latency_ms=float(r.get("latency_ms", 0) or 0),
                    weight=float(r.get("weight", 1.0) or 1.0))
                for r in baseline_data.get("results", [])
            ]
            diff = compare_reports(baseline, report)
            print(_c("\n  Comparison vs baseline\n", "cyan"))
            print(f"    accuracy   : {diff['baseline_accuracy']}% → "
                  f"{diff['candidate_accuracy']}% "
                  f"({diff['accuracy_delta']:+}%)")
            print(f"    avg latency: {diff['latency_delta_ms']:+} ms")
            for entry in diff["regressions"]:
                print(_c(f"    regression : {entry['name']} "
                         f"({entry['change']})", "amber"))
            for entry in diff["improvements"]:
                print(_c(f"    improvement: {entry['name']} "
                         f"({entry['change']})", "green"))

        if args.json:
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report.to_json(), encoding="utf-8")
            print(_c(f"\n  Report written to {out}\n", "green"))
        else:
            print()
    finally:
        rt.stop()
    return 0


def _default_eval_suite():
    """A small built-in suite that checks persona and routing invariants."""
    from ..bench import EvalCase, EvalSuite

    return EvalSuite(
        name="core",
        cases=[
            EvalCase(
                name="salutation_user",
                prompt="Introduce yourself briefly.",
                expect_contains=["Psycho"],
                weight=1.0,
            ),
            EvalCase(
                name="no_robotic_filler",
                prompt="Introduce yourself briefly.",
                expect_not_contains=["As an AI language model", "I cannot"],
                weight=1.0,
            ),
            EvalCase(
                name="non_empty_response",
                prompt="Say hello.",
                min_tokens=1,
                weight=1.0,
            ),
            EvalCase(
                name="math_response",
                prompt="What is 2 + 2? Answer with the number.",
                expect_regex=[r"\b4\b"],
                weight=1.0,
            ),
        ],
    )


def cmd_skill(args: argparse.Namespace) -> int:
    """Manage the local skills catalog."""
    from ..skills import SkillStore

    root = args.skills_dir or str(Path.home() / ".psycho_carla" / "skills")
    store = SkillStore(root)

    if args.skill_cmd == "list":
        skills = store.catalog.list()
        if not skills:
            print(_c(f"\n  No skills installed in {store.root}\n", "dim"))
            print(_c("  Install one with: carla skill install <path-or-url>\n", "dim"))
            return 0
        print(_c(f"\n  Skills ({len(skills)}) in {store.root}\n", "cyan"))
        for skill in skills:
            flag = "" if skill["valid"] else _c("  [invalid]", "amber")
            print(f"  {_c(skill['name'], 'cyan'):<28} {flag}")
            desc = skill["description"] or _c("(no description)", "dim")
            print(f"    {desc}")
            if skill["errors"]:
                for err in skill["errors"]:
                    print(_c(f"    ! {err}", "amber"))
        print()
        return 0

    if args.skill_cmd == "install":
        try:
            skill = store.install(
                args.source, name=args.name, ref=args.ref, overwrite=args.overwrite
            )
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Install failed: {exc}\n", "amber"))
            return 1
        print(_c(f"\n  Installed skill '{skill.name}'\n", "green"))
        print(f"    {skill.description}")
        print(f"    path: {skill.path}")
        print()
        return 0

    if args.skill_cmd == "sync":
        results = store.sync(name=args.name)
        print(_c("\n  Skill sync\n", "cyan"))
        for name in results["synced"]:
            print(_c(f"    updated  {name}", "green"))
        for item in results["skipped"]:
            print(f"    skipped  {item['name']} ({item['reason']})")
        for item in results["failed"]:
            print(_c(f"    failed   {item['name']}: {item['error']}", "amber"))
        print()
        return 0

    if args.skill_cmd == "remove":
        ok = store.uninstall(args.name)
        if ok:
            print(_c(f"\n  Removed skill '{args.name}'\n", "green"))
            return 0
        print(_c(f"\n  No skill named '{args.name}'\n", "amber"))
        return 1

    if args.skill_cmd == "validate":
        result = store.validate()
        print(_c("\n  Skill validation\n", "cyan"))
        print(f"    installed : {result['installed']}")
        print(f"    valid     : {result['valid']}")
        print(f"    invalid   : {result['invalid']}")
        for issue in result["issues"]:
            print(_c(f"\n  {issue['name']}", "amber"))
            for err in issue["errors"]:
                print(_c(f"    • {err}", "dim"))
        print()
        return 0 if not result["invalid"] else 1

    if args.skill_cmd == "search":
        matches = store.catalog.search(args.query, limit=args.limit)
        if not matches:
            print(_c(f"\n  No skills matched '{args.query}'\n", "dim"))
            return 0
        print(_c("\n  Matching skills\n", "cyan"))
        for match in matches:
            print(f"  {_c(match.skill.name, 'cyan'):<28} score {match.score:.2f}")
            print(f"    {match.skill.description}")
        print()
        return 0

    if args.skill_cmd == "show":
        skill = store.catalog.get(args.name)
        if skill is None:
            print(_c(f"\n  No skill named '{args.name}'\n", "amber"))
            return 1
        print(_c(f"\n  {skill.name}\n", "cyan"))
        print(f"    {skill.description}")
        if skill.license:
            print(f"    license      : {skill.license}")
        if skill.version:
            print(f"    version      : {skill.version}")
        if skill.author:
            print(f"    author       : {skill.author}")
        if skill.compatibility:
            print(f"    compatibility: {skill.compatibility}")
        if skill.allowed_tools:
            print(f"    allowed-tools: {' '.join(skill.allowed_tools)}")
        print(f"    path         : {skill.path}")
        print()
        return 0

    if args.skill_cmd == "run":
        from ..skills import ensure_bundled
        from ..skills.executor import SkillExecutor
        from ..tools.registry import get_registry, load_builtin_tools

        ensure_bundled(store)
        store.reload()
        try:
            inputs = json.loads(args.inputs or "{}")
        except Exception:  # noqa: BLE001
            print(_c("\n  Inputs must be a JSON object.\n", "amber"))
            return 2
        load_builtin_tools()
        print()
        print(SkillExecutor(get_registry()).run(store, args.name, inputs))
        print()
        return 0

    if args.skill_cmd == "sources":
        from ..skills.sources import BundledSource

        if args.action == "sync":
            installed = BundledSource().install_all(store, overwrite=True)
            print(_c(f"\n  Bundled source synced: {len(installed)} skills.\n",
                     "green"))
            return 0
        bundled = BundledSource().list()
        print(_c("\n  Skill sources\n", "cyan"))
        print(f"    bundled: {len(bundled)} skills "
              f"(install-on-first-use)")
        print(_c("    Remote sources: [skills].sources in the config "
                 "(GitHub owner/repo or index URL)\n", "dim"))
        return 0
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Run the security scanners over a file, directory, or literal text."""
    from ..security.scanners import scan_text

    targets: list[tuple[str, str]] = []
    if args.text:
        targets.append(("<text>", args.text))
    elif args.path:
        root = Path(args.path).expanduser()
        if root.is_file():
            targets.append((str(root), root.read_text(
                encoding="utf-8", errors="replace")))
        elif root.is_dir():
            for file_path in sorted(root.rglob("*")):
                if file_path.is_file() and file_path.suffix.lower() in (
                        ".py", ".toml", ".md", ".txt", ".json", ".js", ".ts"):
                    targets.append((str(file_path), file_path.read_text(
                        encoding="utf-8", errors="replace")))
        else:
            print(_c(f"\n  Not found: {args.path}\n", "amber"))
            return 1
    if not targets:
        print(_c("\n  Give a path or --text to scan.\n", "amber"))
        return 2

    total = 0
    print(_c("\n  Security scan\n", "cyan"))
    for label, text in targets:
        result = scan_text(text, injection=True)
        if result.clean:
            continue
        total += len(result.findings)
        print(_c(f"  {label}", "bold"))
        for finding in result.findings[:20]:
            print(_c(f"    [{finding.severity}] {finding.scanner}/"
                     f"{finding.kind}: {finding.snippet}", "amber"))
    if total == 0:
        print(_c("  Clean — no findings.\n", "green"))
    else:
        print(_c(f"\n  {total} finding(s).\n", "amber"))
    return 0 if total == 0 else 1


def cmd_telemetry(args: argparse.Namespace) -> int:
    """Show telemetry summary (calls, tokens, energy) from the local store."""
    rt = _runtime(args, with_voice=False)
    try:
        summary = rt.telemetry.summary(window_minutes=args.window)
    finally:
        pass
    print(_c("\n  Telemetry\n", "cyan"))
    print(json.dumps(summary, indent=2, default=str)[:4000])
    print()
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    """Produce the morning digest now."""
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        from ..tools.builtin.knowledge_media import digest_collect

        material = digest_collect(since_hours=args.hours)
        agent = rt.get_agent("morning_digest")
        if agent is None:
            print(_c("\n  morning_digest agent unavailable.\n", "amber"))
            return 1
        result = agent.run(
            "Produce the digest from this collected material:\n\n" + material)
        print("\n" + (result.text or "") + "\n")
    finally:
        rt.stop()
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """Run the deep-research agent on a topic."""
    rt = _runtime(args, with_voice=False)
    rt.start()
    try:
        agent = rt.get_agent("deep_research")
        if agent is None:
            print(_c("\n  deep_research agent unavailable.\n", "amber"))
            return 1
        result = agent.run(args.topic)
        print("\n" + (result.text or "") + "\n")
    finally:
        rt.stop()
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    """First-run shortcuts: init (if needed) + diagnosis summary."""
    class _InitArgs:
        config = args.config
        list_presets = False
        force = False
        preset = ""

    try:
        cmd_init(_InitArgs())
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        print(_c(f"  Init step skipped: {exc}", "dim"))
    print(_c("  Running diagnosis...\n", "dim"))
    return cmd_doctor(args)


def cmd_operators(args: argparse.Namespace) -> int:
    """List operator manifests (upstream `operators` command)."""
    cfg = load_config(args.config)
    ops = cfg.operators
    print(_c("\n  Operators\n", "cyan"))
    print(f"    enabled       : {ops.enabled}")
    print(f"    manifests_dir : {ops.manifests_dir or '(default)'}")
    print(f"    auto_activate : {ops.auto_activate}")
    folder = Path(ops.manifests_dir).expanduser() if ops.manifests_dir \
        else cfg.home / "operators"
    manifests = sorted(folder.glob("*.toml")) if folder.is_dir() else []
    if manifests:
        for item in manifests:
            print(f"    - {item.name}")
    else:
        print(_c(f"    (no manifests in {folder})", "dim"))
    print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """One-screen status: config, tiers, registries, models, daemon."""
    from ..intelligence import model_manager

    cfg = load_config(args.config)
    print(_c("\n  PSYCHO-C.A.R.L.A. status\n", "cyan"))
    print(f"    config      : {cfg.config_path} "
          f"({'ok' if Path(cfg.config_path).exists() else 'missing'})")
    print(f"    home        : {cfg.home}")
    print(f"    default tier: {cfg.intelligence.default_tier}")
    tiers = ", ".join(
        f"{a}={t.provider}/{t.model or '?'}"
        for a, t in list(cfg.intelligence.tiers.items())[:4])
    print(f"    tiers       : {tiers or '(none)'}")
    try:
        local = model_manager.local_models()
        print(f"    models local: {len(local)}")
    except Exception as exc:  # noqa: BLE001
        print(_c(f"    models      : unavailable ({exc})", "amber"))
    try:
        from ..daemon import GatewayDaemon

        daemon = GatewayDaemon(cfg)
        state = daemon.status() if hasattr(daemon, "status") else "unknown"
        print(f"    daemon      : {state}")
    except Exception as exc:  # noqa: BLE001
        print(_c(f"    daemon      : unavailable ({exc})", "amber"))
    print()
    return 0


def cmd_workflow(args: argparse.Namespace) -> int:
    """Run and inspect workflow definitions."""
    from ..workflow import WorkflowEngine, load_workflow, load_workflow_dir

    if args.workflow_cmd == "list":
        if args.dir:
            directory = args.dir
        else:
            # Prefer the bundled examples; fall back to a local folder.
            from ..examples import workflows_dir

            bundled = workflows_dir()
            directory = (
                str(bundled) if bundled.is_dir() else "examples/workflows"
            )
        graphs = load_workflow_dir(directory)
        if not graphs:
            print(_c(f"\n  No workflows found in {directory}\n", "dim"))
            return 0
        print(_c(f"\n  Workflows in {directory}\n", "cyan"))
        for name, graph in sorted(graphs.items()):
            valid, msg = graph.validate()
            flag = "" if valid else _c("  [invalid]", "amber")
            print(f"  {_c(name, 'cyan'):<28} {len(graph.nodes)} nodes, "
                  f"{len(graph.edges)} edges{flag}")
            if not valid:
                print(_c(f"    {msg}", "amber"))
        print()
        return 0

    if args.workflow_cmd == "show":
        try:
            graph = load_workflow(args.file)
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Could not load workflow: {exc}\n", "amber"))
            return 1
        print(_c(f"\n  {graph.name}\n", "cyan"))
        valid, msg = graph.validate()
        print(f"    valid : {valid}" + (f" ({msg})" if msg else ""))
        print(f"    nodes : {len(graph.nodes)}")
        for node in graph.nodes:
            extra = ""
            if node.agent:
                extra = f" agent={node.agent}"
            if node.condition_expr:
                extra = f" condition={node.condition_expr!r}"
            print(f"      {node.id:<16} {node.node_type.value}{extra}")
        print(f"    edges : {len(graph.edges)}")
        for edge in graph.edges:
            cond = f"  if {edge.condition}" if edge.condition else ""
            print(f"      {edge.source} → {edge.target}{cond}")
        print(f"    stages: {graph.execution_stages()}")
        print()
        return 0

    if args.workflow_cmd == "validate":
        try:
            graph = load_workflow(args.file)
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Invalid workflow: {exc}\n", "amber"))
            return 1
        valid, msg = graph.validate()
        if valid:
            print(_c(f"\n  '{graph.name}' is valid.\n", "green"))
            if msg:
                print(_c(f"    {msg}\n", "dim"))
            return 0
        print(_c(f"\n  '{graph.name}' is invalid: {msg}\n", "amber"))
        return 1

    if args.workflow_cmd == "run":
        try:
            graph = load_workflow(args.file)
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Could not load workflow: {exc}\n", "amber"))
            return 1
        prompt = args.input or (
            input(_c("  Input: ", "cyan")) if sys.stdin.isatty() else ""
        )
        rt = _runtime(args, with_voice=False)
        rt.start()
        try:
            print(_c(f"\n  Running '{graph.name}'...\n", "cyan"))
            result = WorkflowEngine().run(graph, rt, initial_input=prompt)
            for step in result.steps:
                status = "ok" if step.success else "FAIL"
                print(f"  [{status}] {step.node_id} "
                      f"({step.duration_seconds:.2f}s)")
                if step.output:
                    print(textwrap.indent(step.output[:500], "      "))
            print()
            print(_c(f"  {'Succeeded' if result.success else 'Failed'} "
                     f"in {result.total_duration_seconds:.2f}s\n", "green"))
        finally:
            rt.stop()
        return 0

    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    """Manage connector connections."""
    from ..connectors import (
        OAUTH_PROVIDERS,
        connector_status,
        delete_tokens,
        get_provider_for_connector,
        run_connector_oauth,
    )
    from ..connectors.oauth import CONNECTORS_DIR

    if args.connect_cmd == "list":
        print(_c("\n  Connectors\n", "cyan"))
        for provider in OAUTH_PROVIDERS.values():
            print(f"  {_c(provider.display_name, 'cyan')} "
                  f"({provider.name})")
            print(f"    covers   : {', '.join(provider.connector_ids)}")
            for connector_id in provider.connector_ids:
                status = connector_status(connector_id)
                flag = _c("connected", "green") if status["connected"] else _c("not connected", "dim")
                print(f"      {connector_id:<16} {flag}")
            print()
        from ..connectors import available_connectors, get_connector

        covered = {cid for p in OAUTH_PROVIDERS.values() for cid in p.connector_ids}
        others = [c for c in available_connectors() if c not in covered]
        if others:
            print(_c("  Local / API-key connectors\n", "cyan"))
            for connector_id in others:
                connector = get_connector(connector_id)
                status = connector.status() if connector is not None else None
                value = status.value if hasattr(status, "value") else str(status)
                print(f"      {connector_id:<22} {value}")
            print()
        return 0

    if args.connect_cmd == "status":
        status = connector_status(args.connector)
        print(_c(f"\n  {args.connector}\n", "cyan"))
        if not status.get("provider"):
            print(_c(f"  No OAuth provider for '{args.connector}'.\n", "amber"))
            return 1
        for key in ("provider", "display_name", "connected", "expired",
                    "has_refresh_token"):
            print(f"    {key:<20} {status.get(key)}")
        if not status.get("connected"):
            print(_c(f"\n  Connect it with: carla connect login {args.connector}\n", "dim"))
        print()
        return 0

    if args.connect_cmd == "login":
        provider = get_provider_for_connector(args.connector)
        if provider is None:
            print(_c(f"\n  No OAuth provider for '{args.connector}'.\n", "amber"))
            return 1
        print(_c(f"\n  Connecting {provider.display_name} "
                 f"({args.connector})\n", "cyan"))
        print(f"    If no browser opens, visit:\n    {provider.setup_url}")
        print(f"    {provider.setup_hint}\n")
        try:
            run_connector_oauth(
                args.connector,
                args.client_id,
                args.client_secret,
                open_browser=not args.no_browser,
            )
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Connection failed: {exc}\n", "amber"))
            return 1
        print(_c(f"\n  Connected. Credentials saved to {CONNECTORS_DIR}\n", "green"))
        return 0

    if args.connect_cmd == "sync":
        from ..connectors import sync_connector as _sync_connector
        from ..connectors import KnowledgeStore

        cfg = load_config(getattr(args, "config", None))
        store = KnowledgeStore(cfg.data_dir / "knowledge.db")
        kwargs: dict = {"limit": args.limit}
        if args.vault:
            kwargs["vault_path"] = args.vault
        try:
            count = _sync_connector(args.connector, store, **kwargs)
        except Exception as exc:
            print(_c(f"\n  Sync failed: {exc}\n", "amber"))
            return 1
        print(_c(f"\n  Synced {args.connector}: {count} document(s) indexed.\n",
                 "green"))
        return 0

    if args.connect_cmd == "logout":
        provider = get_provider_for_connector(args.connector)
        if provider is None:
            print(_c(f"\n  No OAuth provider for '{args.connector}'.\n", "amber"))
            return 1
        removed = 0
        for filename in provider.credential_files:
            if delete_tokens(CONNECTORS_DIR / filename):
                removed += 1
        print(_c(f"\n  Removed {removed} credential file(s) for "
                 f"{provider.display_name}.\n", "green"))
        return 0
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run and install the background daemon."""
    from ..daemon import (
        GatewayDaemon,
        install_service,
        service_instructions,
        uninstall_service,
    )

    if args.daemon_cmd == "start":
        daemon = GatewayDaemon(load_config(args.config))
        print(_c("\n  Starting the console daemon...\n", "cyan"))
        if args.background:
            daemon.start(background=True)
            print(_c("  Started in the background.\n", "green"))
            print(_c("  Logs: ~/.psycho_carla/logs/\n", "dim"))
            return 0
        daemon.install_signal_handlers()
        daemon.start()
        return 0

    if args.daemon_cmd == "status":
        daemon = GatewayDaemon(load_config(args.config))
        status = daemon.status()
        print(_c("\n  Daemon\n", "cyan"))
        for key, value in status.items():
            print(f"    {key:<20} {value}")
        print()
        return 0

    if args.daemon_cmd == "install":
        try:
            path = install_service()
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  Could not install the service: {exc}\n", "amber"))
            return 1
        print(_c(f"\n  Service file written to {path}\n", "green"))
        print(_c("  Next steps:\n", "cyan"))
        for line in service_instructions().get(sys.platform.lower(), "").splitlines():
            print(f"    {line}")
        print()
        return 0

    if args.daemon_cmd == "uninstall":
        ok = uninstall_service()
        print(_c("\n  Service file removed.\n" if ok
                 else "\n  No service file found.\n", "green" if ok else "dim"))
        return 0
    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    """Inspect the optional mining subsystem."""
    from ..mining import detect_all, select_provider
    from ..mining.providers import ensure_registered

    ensure_registered()
    hw = hardware_summary()
    engine_id = getattr(args, "engine", "") or "ollama"
    model = getattr(args, "model", "") or ""

    if args.mine_cmd == "doctor":
        print(_c("\n══ MINING DOCTOR ══\n", "cyan"))
        caps = detect_all(hw, engine_id, model)
        for provider_id, cap in sorted(caps.items()):
            icon = _c("✓", "green") if cap.supported else _c("✗", "red")
            print(f"    {icon} {provider_id:<22} {cap.reason or 'available'}")
        print()
        return 0

    if args.mine_cmd == "providers":
        print(_c("\n  Mining providers\n", "cyan"))
        for provider_id, cap in sorted(detect_all(hw, engine_id, model).items()):
            state = _c("available", "green") if cap.supported else _c("unavailable", "dim")
            print(f"  {_c(provider_id, 'cyan'):<24} {state}")
            if cap.reason:
                print(_c(f"      {cap.reason}", "dim"))
        print()
        return 0

    if args.mine_cmd == "select":
        chosen, caps = select_provider(hw, engine_id, model)
        if chosen is None:
            print(_c("\n  No mining provider can run here.\n", "amber"))
            for provider_id, cap in sorted(caps.items()):
                print(f"    {provider_id:<22} {cap.reason or ''}")
            print()
            return 1
        print(_c(f"\n  Selected provider: {chosen}\n", "green"))
        return 0
    return 0


def cmd_recipes(args: argparse.Namespace) -> int:
    """List and inspect the bundled recipes."""
    from ..recipes import (
        discover_recipes,
        recipe_to_config_overrides,
        resolve_recipe,
    )

    if args.recipes_cmd == "list":
        recipes = discover_recipes()
        print(_c("\n  Recipes\n", "cyan"))
        for name, path in sorted(recipes.items()):
            recipe = resolve_recipe(name)
            kind = recipe.kind if recipe else "?"
            description = recipe.description if recipe else ""
            print(f"  {_c(name, 'cyan'):<24} {_c(f'[{kind}]', 'dim'):<14} {description}")
        print()
        return 0

    if args.recipes_cmd == "show":
        recipe = resolve_recipe(args.name)
        if recipe is None:
            print(_c(f"\n  No recipe named '{args.name}'.\n", "amber"))
            return 1
        print(_c(f"\n  {recipe.name}\n", "cyan"))
        print(f"    {recipe.description}")
        print(f"    kind        {recipe.kind}")
        print(f"    version     {recipe.version}")
        print(f"    agent       {recipe.agent_type} "
              f"(max_turns={recipe.max_turns}, temp={recipe.temperature})")
        print(f"    tools       {', '.join(recipe.tools) or '(none)'}")
        if recipe.eval_suites:
            print(f"    eval suites {', '.join(recipe.eval_suites)}")
        if recipe.schedule_value:
            print(f"    schedule    {recipe.schedule_type}: {recipe.schedule_value}")
        if recipe.channels:
            print(f"    channels    {', '.join(recipe.channels)}")
        print(_c("\n  Config overrides\n", "bold"))
        for key, value in recipe_to_config_overrides(recipe).items():
            print(f"    {key}: {value}")
        print()
        return 0
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    """Inspect and manage cross-channel sessions."""
    from ..sessions import SessionStore

    cfg = load_config(args.config)
    if not cfg.sessions.enabled:
        print(_c("\n  Sessions are disabled in the configuration.\n", "amber"))
        return 1

    store = SessionStore(
        cfg.sessions_db,
        max_age_hours=cfg.sessions.max_age_hours,
        consolidation_threshold=cfg.sessions.consolidation_threshold,
        max_message_tokens=cfg.sessions.max_message_tokens,
    )
    try:
        if args.sessions_cmd == "list":
            sessions = store.list_sessions(limit=args.limit)
            print(_c("\n  Sessions\n", "cyan"))
            if not sessions:
                print(_c("  (none)\n", "dim"))
                return 0
            for s in sessions:
                print(f"  {_c(s['session_id'], 'cyan'):<20} "
                      f"{s['display_name'] or s['user_id'] or '(anonymous)':<18} "
                      f"last activity {time.strftime('%Y-%m-%d %H:%M', time.localtime(s['last_activity']))}")
            print()
            return 0

        if args.sessions_cmd == "show":
            session = store.get(args.session_id)
            if session is None:
                print(_c(f"\n  No session '{args.session_id}'.\n", "amber"))
                return 1
            print(_c(f"\n  Session {session.session_id}\n", "cyan"))
            if session.identity:
                print(f"    user    {session.identity.display_name or session.identity.user_id}")
                for channel, cid in session.identity.channel_ids.items():
                    print(f"    {channel:<8} {cid}")
            print(f"    messages {session.message_count}")
            for anchor in store.anchors(session.session_id):
                print(_c(f"\n    [anchor] {anchor['content'][:300]}\n", "dim"))
            for message in session.messages[-args.limit:]:
                print(f"    {_c(message.role, 'cyan'):<12} {message.content[:160]}")
            print()
            return 0

        if args.sessions_cmd == "consolidate":
            report = store.consolidate(args.session_id)
            if report.get("consolidated"):
                print(_c(f"\n  Consolidated {report['consolidated']} message(s) "
                         f"into 1 anchor. Kept {report.get('recent_kept', 0)} recent.\n",
                         "green"))
            else:
                print(_c(f"\n  Nothing consolidated: {report.get('skipped', 'no session')}\n",
                         "dim"))
            return 0

        if args.sessions_cmd == "expire":
            count = store.expire(args.hours if args.hours else None)
            print(_c(f"\n  Expired {count} session(s).\n", "green"))
            return 0

        if args.sessions_cmd == "stats":
            stats = store.stats()
            print(_c("\n  Session stats\n", "cyan"))
            for key, value in stats.items():
                print(f"    {key:<12} {value}")
            print()
            return 0
    finally:
        store.close()
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Open the holographic HUD as a desktop client."""
    from ..gui import export_desktop_bundle, gui_status, launch_gui

    if args.gui_cmd == "status":
        status = gui_status(args.host, args.port)
        print(_c("\n  HUD status\n", "cyan"))
        for key, value in status.items():
            print(f"    {key:<16} {value}")
        print()
        return 0 if status["reachable"] else 1

    if args.gui_cmd == "export":
        path = export_desktop_bundle(args.output or None)
        size = path.stat().st_size
        print(_c(f"\n  ✓ Wrote {path}  ({size:,} bytes)\n", "green"))
        print(_c("  Open it directly — it runs with no server.\n", "dim"))
        return 0

    if args.gui_cmd == "open":
        result = launch_gui(
            host=args.host,
            port=args.port,
            auto_start=not args.no_start,
            app_mode=not args.browser,
        )
        print(_c(f"\n  HUD: {result['url']}\n", "cyan"))
        print(f"    {result['detail']}")
        if result["started"]:
            print(_c("    The console was started in the background.", "dim"))
        print()
        return 0
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    """The upstream name for the recipes command."""
    from ..recipes import discover_recipes, recipe_to_config_overrides, resolve_recipe

    if args.compose_cmd == "list":
        recipes = discover_recipes()
        print(_c("\n  Compositions\n", "cyan"))
        for name in sorted(recipes):
            recipe = resolve_recipe(name)
            if recipe is None:
                continue
            if args.kind and recipe.kind != args.kind:
                continue
            print(f"  {_c(name, 'cyan'):<24} {_c(f'[{recipe.kind}]', 'dim'):<14} "
                  f"{recipe.description}")
        print()
        return 0

    if args.compose_cmd == "show":
        recipe = resolve_recipe(args.name)
        if recipe is None:
            print(_c(f"\n  No composition named '{args.name}'.\n", "amber"))
            return 1
        print(_c(f"\n  {recipe.name}\n", "cyan"))
        print(f"    {recipe.description}")
        print(f"    kind        {recipe.kind}")
        print(f"    agent       {recipe.agent_type}")
        print(f"    tools       {', '.join(recipe.tools) or '(none)'}")
        if recipe.eval_suites:
            print(f"    eval suites {', '.join(recipe.eval_suites)}")
        if recipe.schedule_value:
            print(f"    schedule    {recipe.schedule_type}: {recipe.schedule_value}")
        print(_c("\n  Overrides\n", "bold"))
        for key, value in recipe_to_config_overrides(recipe).items():
            print(f"    {key}: {value}")
        print()
        return 0
    return 0


def cmd_tunnel(args: argparse.Namespace) -> int:
    """Expose the HUD through a public tunnel."""
    import shutil

    if args.tunnel_cmd == "status":
        print(_c("\n  Tunnel\n", "cyan"))
        if not shutil.which("cloudflared"):
            print(_c("  cloudflared is not installed.\n", "amber"))
            print(_c("  Install it: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n", "dim"))
            return 1
        print(_c("  cloudflared is installed.\n", "green"))
        return 0

    if args.tunnel_cmd == "start":
        if not shutil.which("cloudflared"):
            print(_c("\n  cloudflared is not installed.\n", "amber"))
            print(_c("  Install it: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/\n", "dim"))
            return 1
        print(_c(f"\n  Starting a tunnel to http://127.0.0.1:{args.port}\n", "cyan"))
        print(_c("  The public URL appears below. Note that the tunnel exposes", "dim"))
        print(_c("  the agent's tools — use an API key.\n", "dim"))
        try:
            import subprocess

            subprocess.run(
                ["cloudflared", "tunnel", "--url",
                 f"http://127.0.0.1:{args.port}", "--no-autoupdate"],
                check=False,
            )
        except KeyboardInterrupt:
            print()
        return 0
    return 0


def cmd_engine(args: argparse.Namespace) -> int:
    """Install or inspect C.A.R.L.A.'s own llama.cpp engine."""
    from ..intelligence.llama_engine import engine_status, install_engine

    if args.engine_cmd == "install":
        r = install_engine(args.backend, progress=lambda m, p=None: print(
            f"    {'[%d%%] ' % p if p is not None else ''}{m}"))
        print(_c(f"\n  {'✓' if r['ok'] else '✗'} {r['message']}\n", "green" if r["ok"] else "amber"))
        return 0 if r["ok"] else 1
    st = engine_status()
    print(f"\n  installed: {st['installed']}   backend: {st['backend'] or '-'}   binary: {st['binary'] or '-'}\n")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    """Back up / restore config, memory, sessions, knowledge and skills."""
    from pathlib import Path

    from ..core.backup import create_backup, restore_backup
    from ..core.config import load_config

    home = load_config().home
    if args.backup_cmd == "create":
        out = Path(args.output or time.strftime("carla-backup-%Y%m%d-%H%M%S.zip"))
        out.write_bytes(create_backup(home, include_vault=args.include_vault))
        print(_c(f"\n  ✓ wrote {out}"
                 + (" (includes your API-key vault — keep it private)" if args.include_vault else "")
                 + "\n", "green"))
        return 0
    r = restore_backup(home, Path(args.file).read_bytes())
    print(_c(f"\n  {'✓' if r['ok'] else '✗'} {r['message']}\n", "green" if r["ok"] else "amber"))
    if r.get("previous_state"):
        print(f"    previous files kept in {r['previous_state']}\n")
    return 0 if r["ok"] else 1


def cmd_export(args: argparse.Namespace) -> int:
    """Export conversations (markdown/json) or a fine-tuning dataset (training)."""
    from ..core.config import load_config
    from ..core.exports import export_sessions, export_training_jsonl
    from ..memory.store import MemoryStore

    config = load_config()
    memory = MemoryStore(config.memory_db)
    if args.format == "training":
        text = export_training_jsonl(memory, limit=args.limit)
    else:
        text = export_sessions(memory, args.format, args.session, args.limit)
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(text, encoding="utf-8")
        print(_c(f"\n  ✓ wrote {args.output}\n", "green"))
    else:
        print(text)
    return 0


def cmd_model(args: argparse.Namespace) -> int:
    """Browse the model catalog and download models."""
    from ..intelligence.model_catalog import (
        find_model_spec,
        list_models,
        recommend_models,
    )
    from ..intelligence.model_manager import (
        delete_local_model,
        disk_free_gb,
        local_models,
        pull_model,
    )

    action = args.model_cmd

    if action == "cloud":
        import getpass

        from ..intelligence.model_manager import add_cloud_tier

        key = getpass.getpass("OpenRouter API key (blank to keep the stored one): ") if args.ask_key else ""
        r = add_cloud_tier("openrouter", args.model, tier=args.tier, api_key=key)
        print(_c(f"\n  {'✓' if r['ok'] else '✗'} {r.get('message') or r.get('error')}\n",
                 "green" if r["ok"] else "amber"))
        return 0 if r["ok"] else 1

    if action == "custom":
        from ..intelligence.model_manager import pull_custom

        r = pull_custom(args.engine, args.ref, args.file)
        print(_c(f"\n  {'✓' if r.ok else '✗'} {r.message}\n", "green" if r.ok else "amber"))
        return 0 if r.ok else 1

    if action == "free":
        from ..intelligence.model_lifecycle import lifecycle

        r = lifecycle.free_memory()
        print(_c(f"\n  ✓ {r['message']} — {r['free_gb']} GB free (+{r['freed_gb']} GB)\n", "green"))
        return 0

    if action == "device":
        from ..intelligence.model_manager import set_native_device
        from ..providers.adapters.native_onnx import gpu_status

        if args.device:
            r = set_native_device(args.device)
            print(_c(f"\n  {'✓' if r['ok'] else '✗'} {r.get('message') or r.get('error')}\n",
                     "green" if r["ok"] else "amber"))
            return 0 if r["ok"] else 1
        g = gpu_status()
        print(f"\n  device: {g['device']}   GPU provider available: {g['gpu_available']}"
              f"   providers: {', '.join(g['providers']) or 'onnxruntime not installed'}\n")
        return 0

    if action == "remote":
        from ..intelligence.model_manager import add_remote_tier

        r = add_remote_tier(args.host, args.model, tier=args.tier, kind=args.kind, port=args.port)
        print(_c(f"\n  {'✓' if r['ok'] else '✗'} {r.get('message') or r.get('error')}\n",
                 "green" if r["ok"] else "amber"))
        return 0 if r["ok"] else 1

    if action == "auto":
        from ..intelligence.model_manager import (
            activate_native_model, best_native_model,
        )

        spec = best_native_model()
        if spec is None:
            print(_c("\n  ✗ not enough free RAM for any native model\n", "amber"))
            return 1
        print(_c(f"\n  Best native fit: {spec.name}\n", "cyan"))
        r = pull_model(spec.model_id, engine="native_onnx")
        if not r.ok:
            print(_c(f"  ✗ {r.message}\n", "amber"))
            return 1
        a = activate_native_model(spec.model_id, args.tier)
        print(_c(f"  ✓ {a.get('message') or a.get('error')}\n", "green" if a.get("ok") else "amber"))
        return 0 if a.get("ok") else 1

    if action == "native":
        from ..intelligence.model_manager import activate_native_model

        model_id = args.model_id
        progress_lines: list[str] = []

        def _progress(message: str, percent: float | None = None) -> None:
            progress_lines.append(message)
            if percent is not None:
                print(f"    [{percent:.0f}%] {message}")

        print(_c(f"\n  Downloading {model_id} (runs inside C.A.R.L.A.)\n",
                 "cyan"))
        result = pull_model(model_id, engine="native_onnx", progress=_progress)
        if not result.ok:
            print(_c(f"  ✗ {result.message}\n", "amber"))
            return 1
        print(_c(f"  ✓ {result.message}\n", "green"))
        print(f"    {result.path}")

        activation = activate_native_model(
            result.path.split("/")[-1] if result.path else model_id,
            args.tier,
        )
        if activation.get("ok"):
            print(_c(f"  ✓ {activation['message']}\n", "green"))
        else:
            print(_c(f"  ! {activation.get('error')}\n", "amber"))
        return 0

    if action == "activate":
        from ..intelligence.model_manager import activate_native_model

        activation = activate_native_model(args.model_id, args.tier)
        if not activation.get("ok"):
            print(_c(f"\n  ✗ {activation.get('error')}\n", "amber"))
            return 1
        print(_c(f"\n  ✓ {activation['message']}\n", "green"))
        print(f"    tier    : {activation['tier']}")
        print(f"    model   : {activation['model_id']}")
        print(f"    path    : {activation['model_path']}\n")
        return 0

    if action == "list":
        specs = list_models(
            engine=getattr(args, "engine", None),
            min_ram_gb=getattr(args, "max_ram", None),
        )
        print(_c("\n  Model catalog\n", "cyan"))
        if not specs:
            print("    (no models matched)")
            return 0
        print(f"    {'MODEL ID':<24} {'PARAMS':>7}  {'RAM':>6}  {'DL':>6}  ENGINE(S)")
        print(f"    {'-' * 24} {'-' * 7}  {'-' * 6}  {'-' * 6}  {'-' * 20}")
        for spec in specs:
            ram = f"{spec.ram_gb:g}G" if spec.ram_gb else "-"
            size = f"{spec.disk_size_gb():g}G"
            engines = ",".join(spec.supported_engines[:3])
            print(
                f"    {spec.model_id:<24} {spec.parameter_count_b:>6.1f}B  "
                f"{ram:>6}  {size:>6}  {engines}"
            )
        print()
        return 0

    if action == "info":
        spec = find_model_spec(args.model_id)
        if spec is None:
            print(_c(
                f"\n  '{args.model_id}' is not in the catalog.\n"
                "  Run 'carla model list' to see what is available, or\n"
                "  'carla model pull --engine ollama <tag>' for any Ollama tag.\n",
                "amber",
            ))
            return 1
        print(_c(f"\n  {spec.name}\n", "cyan"))
        rows = [
            ("Model ID", spec.model_id),
            ("Parameters", f"{spec.parameter_count_b}B"),
            ("Context", f"{spec.context_length:,} tokens"),
            ("Provider", spec.provider or "-"),
            ("Quantisation", spec.quantisation or "-"),
            ("Download size", f"{spec.disk_size_gb():g} GB"),
            ("RAM needed", f"{spec.ram_gb:g} GB" if spec.ram_gb else "-"),
            ("VRAM needed", f"{spec.vram_gb:g} GB" if spec.vram_gb else "CPU only"),
            ("Licence", spec.license or "-"),
            ("HF repo", spec.hf_repo or "-"),
            ("GGUF file", spec.gguf_file or "-"),
            ("Engines", ", ".join(spec.supported_engines)),
        ]
        for label, value in rows:
            print(f"    {label:<16} {value}")
        if spec.description:
            print(f"\n    {spec.description}")
        print()
        return 0

    if action == "recommend":
        ram_gb = 0.0
        vram_gb = 0.0
        try:
            import psutil

            ram_gb = round(psutil.virtual_memory().total / 1e9, 1)
        except Exception:
            pass
        try:
            from ..core.energy import hardware_summary

            hw = hardware_summary()
            vram_gb = float(getattr(hw, "vram_gb", 0.0) or 0.0)
        except Exception:
            pass

        free = disk_free_gb()
        specs = recommend_models(ram_gb, vram_gb, limit=args.limit)
        print(_c("\n  Recommended for this machine\n", "cyan"))
        print(f"    RAM {ram_gb:g} GB   VRAM {vram_gb:g} GB   "
              f"disk free {free:g} GB")
        print()
        if not specs:
            print("    No catalogued model fits this machine's RAM.")
            print("    Try 'carla model list --max-ram 0' to see everything.")
            return 0
        for spec in specs:
            fits_disk = spec.disk_size_gb() <= free
            flag = "" if fits_disk else "  (not enough disk space)"
            print(
                f"    {spec.model_id:<24} {spec.ram_gb:>5.1f} GB ram   "
                f"{spec.disk_size_gb():>5.1f} GB dl{flag}"
            )
        print()
        return 0

    if action == "pull":
        print(_c(f"\n  Pulling {args.model_id} for {args.engine}…\n", "cyan"))
        result = pull_model(args.model_id, args.engine)
        if result.ok:
            print(_c(f"  ✓ {result.message}\n", "green"))
            if result.path:
                print(f"    {result.path}")
            print()
            return 0
        print(_c(f"  ✗ {result.message}\n", "red"))
        return 1

    if action == "local":
        entries = local_models()
        print(_c("\n  Models on this machine\n", "cyan"))
        if not entries:
            print("    (none — try 'carla model pull <model>')")
            return 0
        for entry in entries:
            print(
                f"    {entry['model_id']:<28} {entry['engine']:<10} "
                f"{entry['size_gb']:>6.1f} GB"
            )
        print()
        return 0

    if action == "remove":
        result = delete_local_model(args.model_id, args.engine)
        if result.ok:
            print(_c(f"\n  ✓ {result.message}\n", "green"))
            return 0
        print(_c(f"\n  ✗ {result.message}\n", "red"))
        return 1

    return 0


def cmd_channels(args: argparse.Namespace) -> int:
    """Manage messaging channels."""
    from ..channels import ChannelRegistry, create_channel

    action = args.channels_cmd

    if action == "list":
        specs = ChannelRegistry.all()
        print(_c("\n  Channels\n", "cyan"))
        if not specs:
            print("    (none registered)")
            return 0
        print(f"    {'CHANNEL':<24} {'AUTH':<6} DESCRIPTION")
        print(f"    {'-' * 24} {'-' * 6} {'-' * 46}")
        for channel_id, klass in sorted(specs.items()):
            auth = "yes" if getattr(klass, "requires_auth", True) else "no"
            doc = (klass.__doc__ or "").strip().split("\n")[0]
            print(f"    {channel_id:<24} {auth:<6} {doc[:46]}")
        print()
        return 0

    if action == "status":
        specs = ChannelRegistry.all()
        print(_c("\n  Channel status\n", "cyan"))
        if not specs:
            print("    (none registered)")
            return 0
        for channel_id, klass in sorted(specs.items()):
            adapter = create_channel(channel_id, {})
            if adapter is None:
                continue
            state = adapter.status().value
            missing = adapter.missing_credentials()
            note = ""
            if missing:
                note = "  (needs: " + ", ".join(missing) + ")"
            elif not adapter.is_configured():
                note = "  (ready)"
            print(f"    {channel_id:<24} {state:<14}{note}")
        print()
        return 0

    if action == "add":
        channel_id = args.channel
        if ChannelRegistry.get(channel_id) is None:
            print(_c(
                f"\n  Unknown channel '{channel_id}'.\n"
                "  Run 'carla channels list' to see what is available.\n",
                "red",
            ))
            return 1

        # Secrets go to the vault; the config only keeps a reference.
        secret = getattr(args, "secret", None)
        if secret:
            from ..security.vault import Vault

            try:
                vault = Vault()
                vault.set(f"channel_{channel_id}", secret)
                print(_c(f"\n  ✓ Stored the {channel_id} secret in the vault.\n",
                         "green"))
            except Exception as exc:  # noqa: BLE001
                print(_c(f"\n  ✗ Could not store the secret: {exc}\n", "red"))
                return 1

        options: dict[str, object] = {}
        for pair in (getattr(args, "option", None) or []):
            if "=" not in pair:
                continue
            key, _, value = pair.partition("=")
            options[key.strip()] = value.strip()
        if secret:
            options["token"] = secret
        if getattr(args, "channel_id", None):
            options["channel"] = args.channel_id

        cfg = load_config(args.config)
        existing = dict(cfg.channels.channels.get(channel_id, {}) or {})
        existing.update(options)
        cfg.channels.channels[channel_id] = existing
        try:
            save_config(cfg, args.config)
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  ✗ Could not save the config: {exc}\n", "red"))
            return 1

        print(_c(f"\n  ✓ Configured '{channel_id}'.\n", "green"))
        print("    Start the console with 'carla serve' to bring it online.\n")
        return 0

    if action == "remove":
        channel_id = args.channel
        cfg = load_config(args.config)
        if channel_id in cfg.channels.channels:
            del cfg.channels.channels[channel_id]
            save_config(cfg, args.config)
            print(_c(f"\n  ✓ Removed '{channel_id}'.\n", "green"))
            return 0
        print(_c(f"\n  '{channel_id}' was not configured.\n", "amber"))
        return 1

    if action == "send":
        cfg = load_config(args.config)
        options = dict(cfg.channels.channels.get(args.channel, {}) or {})
        adapter = create_channel(args.channel, options)
        if adapter is None:
            print(_c(f"\n  Unknown channel '{args.channel}'.\n", "red"))
            return 1
        try:
            adapter.connect()
            ok = adapter.send(args.target, args.text)
            adapter.disconnect()
        except Exception as exc:  # noqa: BLE001
            print(_c(f"\n  ✗ {exc}\n", "red"))
            return 1
        if ok:
            print(_c(f"\n  ✓ Sent to {args.target} via {args.channel}.\n", "green"))
            return 0
        print(_c(f"\n  ✗ Could not send via {args.channel}.\n", "red"))
        return 1

    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    """Show what is registered: agents, tools, tiers and adapters."""
    cfg = load_config(args.config)
    try:
        rt = CarlaRuntime(cfg, with_voice=False, with_mcp=False)
    except Exception as exc:  # noqa: BLE001
        print(_c(f"\n  Could not start the runtime: {exc}\n", "amber"))
        return 1

    if args.registry_cmd == "list":
        print(_c("\n  Registries\n", "cyan"))
        print(f"    agents    {len(cfg.agent.agents)} configured")
        print(f"    tools     {len(rt.tools.names())}")
        print(f"    tiers     {len(cfg.intelligence.tiers)}")
        from ..providers.base import available_adapters

        print(f"    adapters  {len(available_adapters())}")
        print()
        return 0

    if args.registry_cmd == "show":
        what = args.what
        if what == "tools":
            for t in rt.tools.describe():
                namespace = t.get("namespace", "")
                label = _c(f"[{namespace}]", "dim")
                print(f"  {_c(t.get('name', ''), 'cyan'):<22} {label:<12} "
                      f"{(t.get('description') or '')[:56]}")
        elif what == "tiers":
            for alias, tier in cfg.intelligence.tiers.items():
                print(f"  {_c(alias, 'cyan'):<16} {tier.provider:<12} "
                      f"{tier.model or '(unset)'}")
        elif what == "agents":
            for name, override in cfg.agent.agents.items():
                print(f"  {_c(name, 'cyan'):<20} {override.get('description', '')}")
        elif what == "adapters":
            from ..providers.base import available_adapters

            for name in available_adapters():
                print(f"  {_c(name, 'cyan')}")
        else:
            print(_c(f"\n  Unknown registry '{what}'.\n", "amber"))
            print(_c("  Try: tools, tiers, agents, adapters\n", "dim"))
            return 1
        print()
        return 0
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    """Rate the agent's recent exchanges and inspect the scores."""
    from ..learning.traces import TraceLogger

    cfg = load_config(args.config)
    traces = TraceLogger(cfg.traces_db)

    if args.feedback_cmd == "score":
        if traces.rate(args.trace_id, args.score, args.note or ""):
            print(_c(f"\n  ✓ Rated trace {args.trace_id} as {args.score}\n", "green"))
            return 0
        print(_c(f"\n  No trace {args.trace_id}.\n", "amber"))
        return 1

    if args.feedback_cmd == "thumbs":
        score = 1.0 if args.up else -1.0
        if traces.rate(args.trace_id, score, "thumbs up" if args.up else "thumbs down"):
            print(_c(f"\n  ✓ Rated trace {args.trace_id} "
                     f"{'up' if args.up else 'down'}\n", "green"))
            return 0
        print(_c(f"\n  No trace {args.trace_id}.\n", "amber"))
        return 1

    if args.feedback_cmd == "stats":
        print(json.dumps(traces.stats(), indent=2))
        return 0

    if args.feedback_cmd == "list":
        items = traces.recent(limit=args.limit)
        print(_c("\n  Recent traces\n", "cyan"))
        if not items:
            print(_c("  (none)\n", "dim"))
            return 0
        for t in items:
            rating = t.rating
            mark = _c(f"{rating:+.1f}", "green") if rating else _c("  —  ", "dim")
            print(f"  {_c(str(t.id), 'cyan'):<8} {mark} "
                  f"{_c(t.agent, 'dim'):<16} {(t.prompt or '')[:60]}")
        print()
        return 0
    return 0


def cmd_vault(args: argparse.Namespace) -> int:
    """Manage the encrypted local secret store."""
    from ..security import Vault, VaultError, encryption_available

    available, reason = encryption_available()
    if not available:
        print(_c(f"\n  The vault is unavailable: {reason}\n", "amber"))
        return 1

    try:
        vault = Vault()
    except VaultError as exc:
        print(_c(f"\n  {exc}\n", "amber"))
        return 1

    if args.vault_cmd == "set":
        value = args.value
        if value == "":
            import getpass

            value = getpass.getpass(f"  Value for '{args.key}': ")
        vault.set(args.key, value)
        print(_c(f"\n  ✓ Stored '{args.key}'\n", "green"))
        return 0

    if args.vault_cmd == "get":
        value = vault.get(args.key)
        if not value:
            print(_c(f"\n  '{args.key}' is not set.\n", "amber"))
            return 1
        print(value)
        return 0

    if args.vault_cmd == "list":
        keys = vault.keys()
        print(_c("\n  Vault\n", "cyan"))
        if not keys:
            print(_c("  (empty)\n", "dim"))
            return 0
        for key in keys:
            if args.values:
                print(f"  {_c(key, 'cyan'):<28} {vault.get(key)}")
            else:
                print(f"  {_c(key, 'cyan'):<28} {_c('(hidden)', 'dim')}")
        print()
        return 0

    if args.vault_cmd == "remove":
        if vault.remove(args.key):
            print(_c(f"\n  ✓ Removed '{args.key}'\n", "green"))
            return 0
        print(_c(f"\n  '{args.key}' was not set.\n", "amber"))
        return 1
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    """List and inspect the registered tools."""
    cfg = load_config(args.config)
    try:
        rt = CarlaRuntime(cfg, with_voice=False, with_mcp=False)
    except Exception as exc:  # noqa: BLE001
        print(_c(f"\n  Could not start the runtime: {exc}\n", "amber"))
        return 1

    if args.tools_cmd == "list":
        descriptions = rt.tools.describe()
        if args.category:
            descriptions = [
                d for d in descriptions
                if str(d.get("namespace", "")) == args.category
            ]
        print(_c("\n  Tools\n", "cyan"))
        for d in descriptions:
            name = d.get("name", "")
            namespace = d.get("namespace", "")
            summary = (d.get("description") or "").splitlines()[0][:64]
            print(f"  {_c(name, 'cyan'):<22} {_c(f'[{namespace}]', 'dim'):<12} {summary}")
        print(f"\n  {len(descriptions)} tools\n")
        return 0

    if args.tools_cmd == "inspect":
        tool = rt.tools.get(args.name)
        if tool is None:
            print(_c(f"\n  No tool named '{args.name}'.\n", "amber"))
            names = ", ".join(sorted(rt.tools.names())[:12])
            print(_c(f"  Known tools: {names} ...\n", "dim"))
            return 1
        print(_c(f"\n  {tool.name}\n", "cyan"))
        print(f"    namespace   {tool.namespace}")
        print(f"    description {tool.description}")
        print(f"    schema      {json.dumps(tool.parameters, indent=2)[:600]}")
        print()
        return 0
    return 0


def cmd_voice(args: argparse.Namespace) -> int:
    if args.voice_cmd == "audition":
        return _cmd_voice_audition(args)
    rt = _runtime(args, with_voice=True)
    rt.start()
    try:
        if args.voice_cmd == "speak":
            rt.voice.speak(args.text)
        elif args.voice_cmd == "listen":
            print(_c("  Listening…", "cyan"))
            text = rt.voice.listen_once()
            print(_c(f"  Heard: {text}", "cyan"))
        elif args.voice_cmd == "status":
            print(json.dumps(rt.voice.status(), indent=2))
    finally:
        rt.stop()
    return 0


def _voice_samples_dir() -> Path:
    """Locate the generated voice-sample directory.

    The samples are evaluation artefacts — generated by the tools in
    ``tools/`` — not part of the installed package. They are looked for in
    the current directory, in a source checkout, and under the user's
    C.A.R.L.A. home.
    """
    home = Path(os.environ.get("PSYCHO_CARLA_HOME") or (Path.home() / ".psycho_carla"))
    for cand in (
        Path.cwd() / "voice_samples",
        Path(__file__).resolve().parents[3] / "voice_samples",
        home / "voice_samples",
    ):
        if cand.is_dir():
            return cand
    return Path.cwd() / "voice_samples"


def _cmd_voice_audition(args: argparse.Namespace) -> int:
    """List (or serve) the generated voice samples."""
    samples = _voice_samples_dir()
    manifest = samples / "manifest.json"
    if not manifest.exists():
        print(_c("  No samples found.", "yellow"))
        print(f"    Expected: {manifest}")
        print(_c("    Generate them with:", "dim"))
        print("      python3 tools/make_voice_samples.py")
        print("      python3 tools/make_kokoro_voices.py bf_emma b")
        return 1

    entries = json.loads(manifest.read_text(encoding="utf-8"))
    by_voice: dict[str, list[dict]] = {}
    for e in entries:
        by_voice.setdefault(e["label"], []).append(e)

    print(_c("\n  C.A.R.L.A. voice audition", "cyan"))
    print(_c(f"  {len(entries)} samples across {len(by_voice)} voices", "dim"))
    print(_c(f"  directory: {samples}\n", "dim"))
    for label, items in by_voice.items():
        first = items[0]
        print(f"  {_c(label, 'green'):<36} {first['engine']:<7} {first['voice']:<22} "
              f"{len(items)} clips")
    print(_c("\n  Browse them at: /voice  (with 'carla serve' running)\n", "dim"))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    if args.mcp_cmd == "serve":
        from ..tools.mcp.server import build_default_server

        rt = _runtime(args, with_voice=False)
        server = build_default_server(rt)
        print(_c("  MCP server ready on stdio.", "green"), file=sys.stderr)
        server.serve_stdio()
        rt.stop()
        return 0

    if args.mcp_cmd == "connect":
        rt = _runtime(args, with_voice=False)
        rt.start()
        results = rt.mcp.connect_all()
        for name, status in results.items():
            print(f"  {_c(name, 'cyan'):<24} {status}")
        rt.stop()
        return 0

    if args.mcp_cmd == "status":
        rt = _runtime(args, with_voice=False)
        rt.start()
        print(json.dumps(rt.mcp.status(), indent=2))
        rt.stop()
        return 0

    if args.mcp_cmd == "add":
        return _cmd_mcp_add(args)
    return 0


#: The environment variable each well-known server reads its token from.
_MCP_TOKEN_VARS: dict[str, str] = {
    "github": "GITHUB_PERSONAL_ACCESS_TOKEN",
    "slack": "SLACK_BOT_TOKEN",
    "notion": "NOTION_TOKEN",
    "postgres": "POSTGRES_CONNECTION_STRING",
}


#: Well-known MCP servers, so `carla mcp add github` just works.
MCP_PRESETS: dict[str, dict[str, Any]] = {
    "filesystem": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
    },
    "github": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"},
    },
    "slack": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-slack"],
        "env": {"SLACK_BOT_TOKEN": "${SLACK_TOKEN}"},
    },
    "notion": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@notionhq/notion-mcp-server"],
    },
    "postgres": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-postgres"],
        "env": {"POSTGRES_CONNECTION_STRING": "${DATABASE_URL}"},
    },
    "puppeteer": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
    },
    "sqlite": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-sqlite", "--db-path", "./carla.db"],
    },
    "memory": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
    },
    "fetch": {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch"],
    },
}


def _cmd_mcp_add(args: argparse.Namespace) -> int:
    """Quick MCP server setup — writes the server into the config file."""

    cfg = load_config(args.config)
    name = args.name
    if name in cfg.tools.mcp.servers:
        print(_c(f"\n  '{name}' is already configured.\n", "amber"))
        print(_c("  Use --force to replace it.\n", "dim"))
        return 1

    preset = MCP_PRESETS.get(name)
    server: dict[str, Any] = {}
    if preset:
        server = dict(preset)
    elif args.url:
        server = {"transport": "http", "url": args.url}
    elif args.exec_command:
        server = {
            "transport": "stdio",
            "command": args.exec_command,
            "args": list(args.args or []),
        }
    else:
        print(_c(f"\n  '{name}' is not a known server.\n", "amber"))
        print(_c("  Known: " + ", ".join(sorted(MCP_PRESETS)) + "\n", "dim"))
        print(_c("  Or give a command: carla mcp add mysrv --exec npx --args -y @scope/server\n", "dim"))
        return 1

    if args.url:
        server["transport"] = "http"
        server["url"] = args.url
    if args.exec_command:
        server["command"] = args.exec_command
    if args.args:
        server["args"] = list(args.args)
    if args.key:
        # A token goes into the vault, not the config file. The config
        # keeps a reference; the value is written to an environment
        # variable that the MCP client expands at start-up.
        from ..security import Vault, VaultError, encryption_available

        available, reason = encryption_available()
        if not available:
            print(_c(f"\n  The vault is unavailable: {reason}\n", "amber"))
            print(_c("  The key would have to go in the config file; skipping.\n", "dim"))
            return 1
        try:
            vault = Vault()
            vault_key = f"mcp_{name}_key"
            vault.set(vault_key, args.key)
            # Export the secret for this process so the MCP child inherits
            # it, and record the reference in the config.
            env_name = f"PSYCHO_CARLA_MCP_{name.upper()}_KEY"
            os.environ[env_name] = args.key
            # Point the server's own token variable at the vault-backed
            # reference, so the secret itself never lands in the config.
            token_var = _MCP_TOKEN_VARS.get(name)
            if token_var:
                server.setdefault("env", {})[token_var] = "${" + env_name + "}"
            print(_c(f"  Stored the key in the vault as '{vault_key}'.\n", "green"))
        except VaultError as exc:
            print(_c(f"\n  {exc}\n", "amber"))
            return 1

    _write_mcp_server(cfg.config_path, name, server)
    print(_c(f"\n  ✓ Added MCP server '{name}' to {cfg.config_path}\n", "green"))
    print(_c("  Run: carla mcp connect\n", "dim"))
    return 0


def _write_mcp_server(config_path: Path, name: str, server: dict[str, Any]) -> None:
    """Append an MCP server entry to the config file.

    The config is edited textually rather than re-serialised, so the
    user's comments and formatting survive.
    """
    import tomllib

    text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    try:
        tomllib.loads(text)
    except Exception:
        text = ""

    lines = [
        "",
        f"[tools.mcp.servers.{name}]",
    ]
    for key, value in server.items():
        if isinstance(value, dict):
            lines.append(f"[tools.mcp.servers.{name}.{key}]")
            for sub_key, sub_value in value.items():
                lines.append(f"{sub_key} = {json.dumps(sub_value)}")
        elif isinstance(value, list):
            lines.append(f"{key} = {json.dumps(value)}")
        else:
            lines.append(f"{key} = {json.dumps(value)}")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text.rstrip() + "\n" + "\n".join(lines) + "\n",
                           encoding="utf-8")


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    if args.config_cmd == "show":
        print(_c(f"  {cfg.config_path}\n", "dim"))
        if cfg.config_path.exists():
            print(cfg.config_path.read_text(encoding="utf-8"))
        else:
            print(_c("  (no config file yet — run: carla init)", "amber"))
    elif args.config_cmd == "path":
        print(cfg.config_path)
    elif args.config_cmd == "validate":
        print(_c("  ✓ Configuration is valid.", "green"))
        print(_c(f"    tiers: {', '.join(cfg.intelligence.tiers) or 'none'}", "dim"))
        print(_c(f"    agents: {len(cfg.agent.agents)} overrides", "dim"))
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    """Server API-key management."""
    from ..server.auth import (
        API_KEY_ENV_VARS,
        generate_api_key,
        is_loopback,
        load_api_key,
    )

    if args.auth_cmd == "generate-key":
        key = generate_api_key()
        print(_c("\n  Generated API key:\n", "cyan"))
        print(f"    {key}\n")
        print(_c("  Export it before starting the server:", "dim"))
        print(f'    export {API_KEY_ENV_VARS[0]}="{key}"')
        print(_c("\n  On Windows PowerShell:", "dim"))
        print(f'    $env:{API_KEY_ENV_VARS[0]}="{key}"')
        print(_c(
            "\n  Then set api_key_env in [server] and bind to 0.0.0.0 to\n"
            "  expose the console on your network.\n", "dim"))
        return 0

    if args.auth_cmd == "status":
        cfg = load_config(args.config)
        key = load_api_key(getattr(cfg.server, "api_key", ""))
        env_name = getattr(cfg.server, "api_key_env", API_KEY_ENV_VARS[0])
        loop = is_loopback(cfg.server.host)
        print(_c("\n  Server authentication\n", "cyan"))
        print(f"    bind          : {cfg.server.host}:{cfg.server.port}")
        print(f"    network-exposed: {'YES' if not loop else 'no (loopback)'}")
        if key:
            print(f"    api key       : set ({env_name})")
            print(f"    fingerprint   : ...{key[-6:]}")
        else:
            print(_c("    api key       : not set", "amber"))
            print(_c(f"      set {env_name}, or run: carla auth generate-key", "dim"))
        if not loop and not key:
            print(_c(
                "\n  WARNING: binding to a non-loopback address without a key\n"
                "  exposes shell and file tools to the network. The server will\n"
                "  refuse to start.\n", "amber"))
        print()
        return 0
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="carla",
        description="PSYCHO-C.A.R.L.A. — personal AI on personal devices.",
    )
    p.add_argument("--config", help="Path to psycho_carla.toml")
    sub = p.add_subparsers(dest="command")

    # init
    sp = sub.add_parser("init", help="Create a starter configuration")
    sp.add_argument("--force", action="store_true", help="Overwrite an existing config")
    sp.add_argument("--preset", default="",
                    help="Install a named preset (see --list-presets)")
    sp.add_argument("--list-presets", action="store_true",
                    help="List the available presets and exit")
    sp.set_defaults(func=cmd_init)

    # doctor
    sp = sub.add_parser("doctor", help="Diagnose the environment")
    sp.set_defaults(func=cmd_doctor)

    # ask
    sp = sub.add_parser("ask", help="One-shot query")
    sp.add_argument("prompt")
    sp.add_argument("--agent", default="", help="Agent to use")
    sp.add_argument("--tier", default="", help="Capability tier alias")
    sp.add_argument("--session", default="cli")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--no-voice", action="store_true")
    sp.set_defaults(func=cmd_ask)

    # chat
    sp = sub.add_parser("chat", help="Interactive console")
    sp.add_argument("--agent", default="")
    sp.add_argument("--tier", default="")
    sp.add_argument("--session", default="cli")
    sp.add_argument("--no-voice", action="store_true")
    sp.set_defaults(func=cmd_chat)

    # serve
    sp = sub.add_parser("serve", help="Start the HUD and API server")
    sp.add_argument("--host", default="")
    sp.add_argument("--port", type=int, default=0)
    sp.add_argument("--log-level", default="info")
    sp.add_argument("--reload", action="store_true")
    sp.set_defaults(func=cmd_serve)

    # agents
    sp = sub.add_parser("agents", help="List agents")
    sp.set_defaults(func=cmd_agents)

    # tiers
    sp = sub.add_parser("tiers", help="List capability tiers")
    sp.set_defaults(func=cmd_tiers)

    # memory
    sp = sub.add_parser("memory", help="Memory operations")
    msub = sp.add_subparsers(dest="memory_cmd", required=True)
    ms = msub.add_parser("search"); ms.add_argument("query"); ms.add_argument("--limit", type=int, default=10)
    ms = msub.add_parser("list"); ms.add_argument("--limit", type=int, default=30)
    ms = msub.add_parser("store"); ms.add_argument("text")
    ms = msub.add_parser("delete"); ms.add_argument("memory_id", type=int)
    ms = msub.add_parser("clear")
    ms = msub.add_parser("sessions"); ms.add_argument("--limit", type=int, default=20)
    msub.add_parser("stats")
    sp.set_defaults(func=cmd_memory)

    # cron
    sp = sub.add_parser("cron", help="Scheduler operations")
    csub = sp.add_subparsers(dest="cron_cmd", required=True)
    csub.add_parser("list")
    ca = csub.add_parser("add")
    ca.add_argument("name"); ca.add_argument("schedule"); ca.add_argument("--agent", default="morning_digest")
    ca.add_argument("--prompt", default="")
    cr = csub.add_parser("remove"); cr.add_argument("job_id")
    cr = csub.add_parser("run"); cr.add_argument("job_id")
    sp.set_defaults(func=cmd_cron)

    # optimize
    sp = sub.add_parser(
        "optimize",
        help="Run the learning optimizer (routing, prompt, tools, engine)",
    )
    sp.add_argument("--agent", default="")
    sp.add_argument(
        "--surface",
        default="",
        choices=["", "routing", "prompt", "intelligence", "tools",
                 "tool_selection", "engine", "engine_spec", "skills"],
        help="Limit to one learning surface (default: all)",
    )
    sp.add_argument("--tier", default="", help="Limit engine spec to one tier")
    sp.add_argument("--min-samples", type=int, default=5)
    sp.add_argument(
        "--policy", default="dspy",
        help="Optimizer policy for skill optimisation (dspy|heuristic)",
    )
    sp.set_defaults(func=cmd_optimize)

    # bench
    sp = sub.add_parser("bench", help="Run a performance benchmark")
    sp.add_argument(
        "--workload", default="chat", choices=["chat", "tool_use"],
        help="Built-in workload to run",
    )
    sp.add_argument("--workload-file", default="", help="JSON workload file")
    sp.add_argument("--turns", type=int, default=5, help="Number of cases")
    sp.add_argument("--repeat", type=int, default=1)
    sp.add_argument("--tier", default="")
    sp.add_argument("--json", default="", help="Write the report to this path")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_bench)

    # eval
    sp = sub.add_parser("eval", help="Run a quality evaluation suite")
    sp.add_argument("--eval-file", default="", help="JSON eval suite file")
    sp.add_argument("--suite", default="",
                    help="Built-in suite: core, reasoning, knowledge, "
                         "instruction, coding, all")
    sp.add_argument("--list-suites", action="store_true",
                    help="List the built-in suites and exit")
    sp.add_argument("--compare", default="",
                    help="Path to a previous report JSON to diff against")
    sp.add_argument("--json", default="", help="Write the report to this path")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=cmd_eval)

    # voice
    sp = sub.add_parser("voice", help="Voice commands")
    vsub = sp.add_subparsers(dest="voice_cmd", required=True)
    vs = vsub.add_parser("speak"); vs.add_argument("text")
    vsub.add_parser("listen")
    vsub.add_parser("status")
    vsub.add_parser("audition", help="List the generated voice samples")
    sp.set_defaults(func=cmd_voice)

    # tunnel
    sp = sub.add_parser("tunnel", help="Expose the HUD through a public tunnel")
    tnsub = sp.add_subparsers(dest="tunnel_cmd", required=True)
    tnst = tnsub.add_parser("start", help="Open a tunnel to the HUD")
    tnst.add_argument("--port", type=int, default=8787)
    tnsub.add_parser("status", help="Report whether cloudflared is available")
    sp.set_defaults(func=cmd_tunnel)

    # channels
    sp = sub.add_parser(
        "channels",
        help="Manage messaging channels",
        description="Connect C.A.R.L.A. to messaging platforms.",
    )
    chsub = sp.add_subparsers(dest="channels_cmd", required=True)
    chsub.add_parser("list", help="Every available channel")
    chsub.add_parser("status", help="Show each channel's state")
    cha = chsub.add_parser("add", help="Configure a channel")
    cha.add_argument("channel", help="e.g. telegram")
    cha.add_argument("--secret", default="",
                     help="The credential; stored in the encrypted vault")
    cha.add_argument("--channel-id", default="",
                     help="The destination id on that platform")
    cha.add_argument("--option", action="append", default=[],
                     metavar="KEY=VALUE",
                     help="Extra option; repeatable")
    chr_ = chsub.add_parser("remove", help="Remove a channel")
    chr_.add_argument("channel")
    chs = chsub.add_parser("send", help="Send a message through a channel")
    chs.add_argument("channel")
    chs.add_argument("target", help="Chat id, channel id or address")
    chs.add_argument("text")
    sp.set_defaults(func=cmd_channels)

    # registry
    sp = sub.add_parser("registry", help="Show what is registered")
    rgsub = sp.add_subparsers(dest="registry_cmd", required=True)
    rgsub.add_parser("list", help="Summary of every registry")
    rgsh = rgsub.add_parser("show", help="Show one registry in full")
    rgsh.add_argument("what", help="tools | tiers | agents | adapters")
    sp.set_defaults(func=cmd_registry)

    # engine
    sp = sub.add_parser("engine", help="Install or inspect the built-in llama.cpp engine")
    esub = sp.add_subparsers(dest="engine_cmd", required=True)
    ei = esub.add_parser("install", help="Download the official llama.cpp build")
    ei.add_argument("--backend", default="vulkan", choices=["vulkan", "cpu", "hip"],
                    help="vulkan = any GPU (AMD/NVIDIA/Intel); cpu = no GPU; hip = AMD ROCm")
    esub.add_parser("status", help="Is the engine installed?")
    sp.set_defaults(func=cmd_engine)

    # backup
    sp = sub.add_parser("backup", help="Back up or restore your config, memory and skills")
    bsub = sp.add_subparsers(dest="backup_cmd", required=True)
    bc = bsub.add_parser("create", help="Write a backup zip")
    bc.add_argument("-o", "--output", default="")
    bc.add_argument("--include-vault", action="store_true", help="also include saved API keys (encrypted)")
    br = bsub.add_parser("restore", help="Restore a backup zip (current files are kept aside)")
    br.add_argument("file")
    sp.set_defaults(func=cmd_backup)

    # export
    sp = sub.add_parser("export", help="Export conversations or a training dataset")
    sp.add_argument("--format", default="markdown", choices=["markdown", "json", "training"])
    sp.add_argument("--session", default="", help="one session id (default: recent sessions)")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("-o", "--output", default="", help="write to a file instead of printing")
    sp.set_defaults(func=cmd_export)

    # model
    sp = sub.add_parser(
        "model",
        help="Download and manage local models",
        description="Browse the model catalog and download models for a "
                    "local engine.",
    )
    mdlsub = sp.add_subparsers(dest="model_cmd", required=True)
    mdl = mdlsub.add_parser("list", help="Show the model catalog")
    mdl.add_argument("--engine", default=None,
                     help="Only models this engine can serve")
    mdl.add_argument("--max-ram", type=float, default=None,
                     help="Only models that fit this much RAM (GB)")
    mdli = mdlsub.add_parser("info", help="Details for one model")
    mdli.add_argument("model_id", help="e.g. qwen3:4b")
    mdlp = mdlsub.add_parser("pull", help="Download a model")
    mdlp.add_argument("model_id", help="e.g. qwen3:4b")
    mdlp.add_argument("--engine", default="ollama",
                      help="ollama | llamacpp | native_onnx | vllm | sglang")
    mdlr = mdlsub.add_parser("recommend",
                             help="Suggest models for this machine")
    mdlr.add_argument("--limit", type=int, default=5)
    mdlsub.add_parser("local", help="Models already on this machine")
    mdlc = mdlsub.add_parser("custom", help="Download a model that is not in the catalog")
    mdlc.add_argument("ref", help="Ollama tag, or Hugging Face repo owner/name")
    mdlc.add_argument("--engine", default="ollama", help="ollama | llamacpp | native_onnx")
    mdlc.add_argument("--file", default="", help="file inside the repo (.gguf / .onnx)")
    mdlsub.add_parser("free", help="Unload all models and free memory")
    mdlk = mdlsub.add_parser("cloud", help="Use a hosted model through OpenRouter")
    mdlk.add_argument("model", help="e.g. qwen/qwen-2.5-coder-32b-instruct")
    mdlk.add_argument("--tier", default="deep_tier")
    mdlk.add_argument("--ask-key", action="store_true", help="prompt for the API key (stored in the encrypted vault)")
    mdlv = mdlsub.add_parser("device", help="Show or set the native engine device")
    mdlv.add_argument("device", nargs="?", default="", help="cpu | gpu | auto")
    mdlm = mdlsub.add_parser("remote", help="Use a model served by another PC")
    mdlm.add_argument("host")
    mdlm.add_argument("model")
    mdlm.add_argument("--tier", default="deep_tier")
    mdlm.add_argument("--kind", default="ollama", help="ollama | openai")
    mdlm.add_argument("--port", type=int, default=0)
    mdla = mdlsub.add_parser("auto", help="Pick, download and activate the best native model")
    mdla.add_argument("--tier", default="fast_tier")
    mdld = mdlsub.add_parser("remove", help="Delete a downloaded model")
    mdld.add_argument("model_id")
    mdld.add_argument("--engine", default="llamacpp")
    mdlna = mdlsub.add_parser(
        "native",
        help="Download a model that runs inside C.A.R.L.A. (no Ollama)",
    )
    mdlna.add_argument("model_id",
                       help="e.g. native:smollm2-360m")
    mdlna.add_argument("--tier", default="fast_tier",
                       help="The capability tier to point at it")
    mdlac = mdlsub.add_parser(
        "activate",
        help="Point a capability tier at a downloaded native model",
    )
    mdlac.add_argument("model_id")
    mdlac.add_argument("--tier", default="fast_tier")
    sp.set_defaults(func=cmd_model)

    # feedback
    sp = sub.add_parser("feedback", help="Rate the agent's recent exchanges")
    fbsub = sp.add_subparsers(dest="feedback_cmd", required=True)
    fbs = fbsub.add_parser("score", help="Score a trace from -1 to 1")
    fbs.add_argument("trace_id", type=int)
    fbs.add_argument("score", type=float)
    fbs.add_argument("--note", default="", help="A short note")
    fbth = fbsub.add_parser("thumbs", help="Thumbs up or down")
    fbth.add_argument("trace_id", type=int)
    fbth.add_argument("--up", action="store_true", default=True)
    fbth.add_argument("--down", dest="up", action="store_false")
    fbl = fbsub.add_parser("list", help="List recent traces")
    fbl.add_argument("--limit", type=int, default=20)
    fbsub.add_parser("stats", help="Rating statistics")
    sp.set_defaults(func=cmd_feedback)

    # vault
    sp = sub.add_parser("vault", help="The encrypted local secret store")
    vsub = sp.add_subparsers(dest="vault_cmd", required=True)
    vset = vsub.add_parser("set", help="Store a secret")
    vset.add_argument("key")
    vset.add_argument("value", nargs="?", default="",
                      help="Omit to be prompted without echo")
    vget = vsub.add_parser("get", help="Read a secret")
    vget.add_argument("key")
    vlst = vsub.add_parser("list", help="List the stored key names")
    vlst.add_argument("--values", action="store_true",
                      help="Also print the values (they are secrets)")
    vrm = vsub.add_parser("remove", help="Delete a secret")
    vrm.add_argument("key")
    sp.set_defaults(func=cmd_vault)

    # tools
    sp = sub.add_parser("tools", help="List and inspect the registered tools")
    tsub2 = sp.add_subparsers(dest="tools_cmd", required=True)
    tlist = tsub2.add_parser("list", help="List the tools")
    tlist.add_argument("--category", default="",
                       help="Filter by namespace, e.g. memory, files, web")
    tins = tsub2.add_parser("inspect", help="Show one tool in full")
    tins.add_argument("name")
    sp.set_defaults(func=cmd_tools)

    # gui
    sp = sub.add_parser("gui", help="Open the holographic HUD as a desktop client")
    gsub = sp.add_subparsers(dest="gui_cmd", required=True)
    gop = gsub.add_parser("open", help="Start the console and open the HUD")
    gop.add_argument("--host", default="127.0.0.1")
    gop.add_argument("--port", type=int, default=8787)
    gop.add_argument("--no-start", action="store_true",
                     help="Do not start the console if it is not running")
    gop.add_argument("--browser", action="store_true",
                     help="Use the default browser instead of an app window")
    gst = gsub.add_parser("status", help="Report whether the HUD is reachable")
    gst.add_argument("--host", default="127.0.0.1")
    gst.add_argument("--port", type=int, default=8787)
    gex = gsub.add_parser("export", help="Export a standalone HTML bundle")
    gex.add_argument("--output", default="")
    sp.set_defaults(func=cmd_gui)

    # mine
    sp = sub.add_parser("mine", help="The optional mining subsystem")
    msub = sp.add_subparsers(dest="mine_cmd", required=True)
    msub.add_parser("doctor", help="Check whether mining can run here")
    msub.add_parser("providers", help="List mining providers")
    msel = msub.add_parser("select", help="Pick a provider for this machine")
    msel.add_argument("--engine", default="")
    msel.add_argument("--model", default="")
    sp.set_defaults(func=cmd_mine)

    # recipes
    sp = sub.add_parser("recipes", help="Composable primitive configurations")
    rsub2 = sp.add_subparsers(dest="recipes_cmd", required=True)
    rsub2.add_parser("list", help="List the bundled recipes")
    rsh2 = rsub2.add_parser("show", help="Show a recipe")
    rsh2.add_argument("name")
    sp.set_defaults(func=cmd_recipes)

    # compose — the upstream name for the same thing
    sp = sub.add_parser("compose", help="Compose agents and operators (recipes)")
    cmsub = sp.add_subparsers(dest="compose_cmd", required=True)
    cmlist = cmsub.add_parser("list", help="List the composable configurations")
    cmlist.add_argument("--kind", default="",
                        help="Filter by kind: discrete | operator")
    cmshow = cmsub.add_parser("show", help="Show a composition")
    cmshow.add_argument("name")
    sp.set_defaults(func=cmd_compose)

    # sessions
    sp = sub.add_parser("sessions", help="Cross-channel session management")
    ssub = sp.add_subparsers(dest="sessions_cmd", required=True)
    sl = ssub.add_parser("list", help="List sessions")
    sl.add_argument("--limit", type=int, default=20)
    ss = ssub.add_parser("show", help="Show a session")
    ss.add_argument("session_id")
    ss.add_argument("--limit", type=int, default=40)
    sc = ssub.add_parser("consolidate", help="Consolidate a long session")
    sc.add_argument("session_id")
    se = ssub.add_parser("expire", help="Expire idle sessions")
    se.add_argument("--hours", type=float, default=0.0)
    ssub.add_parser("stats", help="Session store statistics")
    sp.set_defaults(func=cmd_sessions)

    # templates
    sp = sub.add_parser("templates", help="Agent template catalogue")
    tsub = sp.add_subparsers(dest="templates_cmd", required=True)
    tsub.add_parser("list", help="List the bundled templates")
    tsh = tsub.add_parser("show", help="Show a template")
    tsh.add_argument("name")
    sp.set_defaults(func=cmd_templates)

    # connect
    sp = sub.add_parser("connect", help="Manage connector connections")
    csub2 = sp.add_subparsers(dest="connect_cmd", required=True)
    csub2.add_parser("list", help="List available connectors")
    cl = csub2.add_parser("login", help="Connect a connector via OAuth")
    cl.add_argument("connector", help="e.g. gmail, gdrive, github")
    cl.add_argument("--client-id", default="")
    cl.add_argument("--client-secret", default="")
    cl.add_argument("--no-browser", action="store_true",
                    help="Print the URL instead of opening a browser")
    cs = csub2.add_parser("status", help="Show a connector's status")
    cs.add_argument("connector")
    cy = csub2.add_parser("sync", help="Sync a connector into the knowledge store")
    cy.add_argument("connector", help="e.g. obsidian, hackernews, gmail")
    cy.add_argument("--vault", default="", help="Obsidian vault path")
    cy.add_argument("--limit", type=int, default=25)
    cr2 = csub2.add_parser("logout", help="Remove a connector's credentials")
    cr2.add_argument("connector")
    sp.set_defaults(func=cmd_connect)

    # daemon
    sp = sub.add_parser("daemon", help="Run the console as a background service")
    dsub = sp.add_subparsers(dest="daemon_cmd", required=True)
    dst = dsub.add_parser("start", help="Start the daemon")
    dst.add_argument("--background", action="store_true",
                     help="Detach and return immediately")
    dsub.add_parser("status", help="Show the daemon status")
    din = dsub.add_parser("install", help="Install the platform service")
    dsub.add_parser("uninstall", help="Remove the platform service")
    sp.set_defaults(func=cmd_daemon)

    # workflow
    sp = sub.add_parser("workflow", help="Run and inspect workflows")
    wsub = sp.add_subparsers(dest="workflow_cmd", required=True)
    wl = wsub.add_parser("list", help="List workflows in a directory")
    wl.add_argument("--dir", default="")
    wsh = wsub.add_parser("show", help="Show a workflow's structure")
    wsh.add_argument("file")
    wv = wsub.add_parser("validate", help="Validate a workflow file")
    wv.add_argument("file")
    wr = wsub.add_parser("run", help="Run a workflow")
    wr.add_argument("file")
    wr.add_argument("--input", default="", help="Initial input text")
    sp.set_defaults(func=cmd_workflow)

    # auth
    sp = sub.add_parser("auth", help="Server API-key management")
    asub = sp.add_subparsers(dest="auth_cmd", required=True)
    asub.add_parser("generate-key", help="Generate an API key for LAN exposure")
    asub.add_parser("status", help="Show the current authentication state")
    sp.set_defaults(func=cmd_auth)

    # skill
    sp = sub.add_parser("skill", help="Manage the skills catalog")
    ssub = sp.add_subparsers(dest="skill_cmd", required=True)
    si = ssub.add_parser("install", help="Install a skill from a path, git URL or URL")
    si.add_argument("source")
    si.add_argument("--name", default="")
    si.add_argument("--ref", default="", help="Git branch or tag")
    si.add_argument("--overwrite", action="store_true")
    si.add_argument("--skills-dir", default="")
    ssub.add_parser("sync", help="Re-fetch installed skills from their sources")
    sl = ssub.add_parser("list", help="List installed skills")
    sl.add_argument("--skills-dir", default="")
    sr = ssub.add_parser("remove", help="Uninstall a skill")
    sr.add_argument("name")
    sr.add_argument("--skills-dir", default="")
    sv = ssub.add_parser("validate", help="Validate skills against the spec")
    sv.add_argument("--skills-dir", default="")
    ss = ssub.add_parser("search", help="Search the catalog")
    ss.add_argument("query")
    ss.add_argument("--limit", type=int, default=10)
    ss.add_argument("--skills-dir", default="")
    sh = ssub.add_parser("show", help="Show one skill's details")
    sh.add_argument("name")
    sh.add_argument("--skills-dir", default="")
    srn = ssub.add_parser("run", help="Run a skill's step pipeline")
    srn.add_argument("name")
    srn.add_argument("inputs", nargs="?", default="{}",
                     help="JSON inputs, e.g. '{\"file_path\": \"x.py\"}'")
    srn.add_argument("--skills-dir", default="")
    ssrc = ssub.add_parser("sources", help="List/sync skill sources")
    ssrc.add_argument("action", nargs="?", default="list",
                      choices=["list", "sync"])
    ssrc.add_argument("--skills-dir", default="")
    sp.set_defaults(func=cmd_skill)

    # mcp
    sp = sub.add_parser("mcp", help="MCP client/server commands")
    mcp_sub = sp.add_subparsers(dest="mcp_cmd", required=True)
    mcp_sub.add_parser("serve")
    mcp_sub.add_parser("connect")
    mcp_sub.add_parser("status")
    mcp_add = mcp_sub.add_parser("add", help="Quick MCP server setup")
    mcp_add.add_argument("name", help="e.g. filesystem, github, slack, memory")
    mcp_add.add_argument("--url", default="", help="HTTP/SSE endpoint")
    # NOT --command: the top-level subparser uses dest="command", and an
    # option of the same name would overwrite the parsed subcommand.
    mcp_add.add_argument("--exec", dest="exec_command", default="",
                          help="Command to run")
    mcp_add.add_argument("--args", nargs="*", default=[], help="Command arguments")
    mcp_add.add_argument("--key", default="", help="Token (stored in the vault)")
    mcp_add.add_argument("--force", action="store_true", help="Replace an existing entry")
    sp.set_defaults(func=cmd_mcp)

    # config
    sp = sub.add_parser("config", help="Configuration utilities")
    cfg_sub = sp.add_subparsers(dest="config_cmd", required=True)
    cfg_sub.add_parser("show")
    cfg_sub.add_parser("path")
    cfg_sub.add_parser("validate")
    sp.set_defaults(func=cmd_config)

    # scan (security scanners)
    sp = sub.add_parser("scan", help="Scan files or text for secrets/PII/injection")
    sp.add_argument("path", nargs="?", default="")
    sp.add_argument("--text", default="")
    sp.set_defaults(func=cmd_scan)

    # telemetry
    sp = sub.add_parser("telemetry", help="Telemetry summary")
    sp.add_argument("--window", type=int, default=1440,
                    help="Window in minutes (default 24h)")
    sp.set_defaults(func=cmd_telemetry)

    # digest / research (agent one-shots)
    sp = sub.add_parser("digest", help="Produce the morning digest now")
    sp.add_argument("--hours", type=float, default=24.0)
    sp.set_defaults(func=cmd_digest)

    sp = sub.add_parser("research", help="Deep-research a topic",
                        aliases=["deep-research-setup"])
    sp.add_argument("topic")
    sp.set_defaults(func=cmd_research)

    # quickstart / operators / status
    sp = sub.add_parser("quickstart", help="First-run setup + diagnosis")
    sp.set_defaults(func=cmd_quickstart)

    sp = sub.add_parser("operators", help="Operator manifests")
    sp.set_defaults(func=cmd_operators)

    sp = sub.add_parser("status", help="One-screen system status")
    sp.set_defaults(func=cmd_status)

    # upstream-name aliases (same handlers, friendlier surface)
    sp = sub.add_parser("scheduler", help="Alias of cron")
    ssub2 = sp.add_subparsers(dest="cron_cmd", required=True)
    ssub2.add_parser("list")
    ca = ssub2.add_parser("add")
    ca.add_argument("name"); ca.add_argument("schedule")
    ca.add_argument("--agent", default="morning_digest")
    ca.add_argument("--prompt", default="")
    cr = ssub2.add_parser("remove"); cr.add_argument("job_id")
    cr = ssub2.add_parser("run"); cr.add_argument("job_id")
    sp.set_defaults(func=cmd_cron)

    sp = sub.add_parser("tool", help="Alias of tools")
    tsub3 = sp.add_subparsers(dest="tools_cmd", required=True)
    tl = tsub3.add_parser("list", help="List the tools")
    tl.add_argument("--category", default="",
                    help="Filter by namespace, e.g. memory, files, web")
    ti = tsub3.add_parser("inspect", help="Show one tool in full")
    ti.add_argument("name")
    sp.set_defaults(func=cmd_tools)

    sp = sub.add_parser("channel", help="Alias of channels")
    csub3 = sp.add_subparsers(dest="channels_cmd", required=True)
    csub3.add_parser("list", help="Every available channel")
    csub3.add_parser("status", help="Show each channel's state")
    cha = csub3.add_parser("add", help="Configure a channel")
    cha.add_argument("channel", help="e.g. telegram")
    cha.add_argument("--secret", default="",
                     help="The credential; stored in the encrypted vault")
    cha.add_argument("--channel-id", default="",
                     help="The destination id on that platform")
    cha.add_argument("--option", action="append", default=[],
                     metavar="KEY=VALUE", help="Extra option; repeatable")
    chr2 = csub3.add_parser("remove", help="Remove a channel")
    chr2.add_argument("channel")
    chs = csub3.add_parser("send", help="Send a message through a channel")
    chs.add_argument("channel")
    chs.add_argument("target", help="Chat id, channel id or address")
    chs.add_argument("text")
    sp.set_defaults(func=cmd_channels)

    return p


def main(argv: list[str] | None = None) -> int:
    # Restart once with espeak-ng on the dynamic loader path, if a local
    # install needs it. Safe here because this is a process entrypoint.
    from ..voice.tts import maybe_reexec_with_espeak

    maybe_reexec_with_espeak()

    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        _print_banner()
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:
        print(_c(f"\n  Error: {type(exc).__name__}: {exc}\n", "red"))
        if os.environ.get("PSYCHO_CARLA_DEBUG"):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
