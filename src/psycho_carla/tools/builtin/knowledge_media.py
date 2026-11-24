"""
Knowledge-graph, media, and remaining runtime-bound tools.

Adds the upstream tool names that the tools and skills layers need:

* Knowledge graph — ``kg_add_entity``, ``kg_add_relation``, ``kg_query``,
  ``kg_neighbors`` over a small SQLite graph in the runtime data dir.
* Memory directory indexing — ``memory_index``, ``memory_retrieve``,
  ``memory_manage`` (skills reference ``memory_index``).
* Voice/media — ``audio_transcribe``, ``text_to_speech``.
* People — ``user_profile_manage`` over the MemoryStore profile kind.
* Skills — ``skill_manage`` (load/run a bundled skill's rendered steps).
* Digest — ``digest_collect`` gathers the day's facts for the digest agent.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..registry import ToolRegistry, get_registry


def _runtime():
    from ...core.state import get_runtime

    return get_runtime()


def _need(name: str) -> str:
    return (f"Error: {name} needs a running runtime; none is active in "
            "this process. Use it via `carla serve`, the HUD, or the CLI.")


# --------------------------------------------------------------------------- #
# Knowledge graph (SQLite, in the runtime data dir)
# --------------------------------------------------------------------------- #

def _kg_connect(runtime: Any) -> sqlite3.Connection:
    db_path = Path(runtime.config.data_dir) / "knowledge_graph.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities "
        "(id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT, props TEXT)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS relations "
        "(id INTEGER PRIMARY KEY, source TEXT, relation TEXT, target TEXT, "
        "props TEXT)")
    return conn


def kg_add_entity(name: str, kind: str = "thing", props: str = "") -> str:
    """Add (or update) an entity in the knowledge graph."""
    runtime = _runtime()
    if runtime is None:
        return _need("kg_add_entity")
    payload = {}
    if props:
        try:
            payload = json.loads(props)
        except Exception:
            return "Error: props must be a JSON object."
    conn = _kg_connect(runtime)
    try:
        conn.execute(
            "INSERT INTO entities (name, kind, props) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, "
            "props=excluded.props",
            (name, kind or "thing", json.dumps(payload)),
        )
        conn.commit()
    finally:
        conn.close()
    return f"Entity '{name}' ({kind or 'thing'}) recorded."


def kg_add_relation(source: str, relation: str, target: str) -> str:
    """Add a directed relation between two knowledge-graph entities."""
    runtime = _runtime()
    if runtime is None:
        return _need("kg_add_relation")
    conn = _kg_connect(runtime)
    try:
        for entity in (source, target):
            conn.execute(
                "INSERT OR IGNORE INTO entities (name, kind, props) "
                "VALUES (?, 'thing', '{}')", (entity,))
        conn.execute(
            "INSERT INTO relations (source, relation, target, props) "
            "VALUES (?, ?, ?, '{}')", (source, relation, target))
        conn.commit()
    finally:
        conn.close()
    return f"Relation recorded: {source} -[{relation}]-> {target}."


def kg_query(query: str, limit: int = 20) -> str:
    """Search knowledge-graph entities and relations by text."""
    runtime = _runtime()
    if runtime is None:
        return _need("kg_query")
    conn = _kg_connect(runtime)
    try:
        like = f"%{query}%"
        entities = conn.execute(
            "SELECT name, kind FROM entities WHERE name LIKE ? OR kind LIKE ? "
            "LIMIT ?", (like, like, int(limit))).fetchall()
        relations = conn.execute(
            "SELECT source, relation, target FROM relations WHERE "
            "source LIKE ? OR target LIKE ? OR relation LIKE ? LIMIT ?",
            (like, like, like, int(limit))).fetchall()
    finally:
        conn.close()
    lines = [f"- {name} ({kind})" for name, kind in entities]
    lines += [f"- {s} -[{r}]-> {t}" for s, r, t in relations]
    return "\n".join(lines) if lines else f"No graph matches for '{query}'."


def kg_neighbors(name: str, depth: int = 1) -> str:
    """List the neighbours of a knowledge-graph entity."""
    runtime = _runtime()
    if runtime is None:
        return _need("kg_neighbors")
    conn = _kg_connect(runtime)
    try:
        seen = {name}
        frontier = {name}
        edges: list[str] = []
        for _ in range(max(1, int(depth))):
            nxt: set[str] = set()
            for node in frontier:
                rows = conn.execute(
                    "SELECT source, relation, target FROM relations WHERE "
                    "source = ? OR target = ?", (node, node)).fetchall()
                for source, relation, target in rows:
                    edges.append(f"- {source} -[{relation}]-> {target}")
                    for other in (source, target):
                        if other not in seen:
                            seen.add(other)
                            nxt.add(other)
            frontier = nxt
            if not frontier:
                break
    finally:
        conn.close()
    if not edges:
        return f"'{name}' has no relations in the graph."
    return "\n".join(sorted(set(edges)))


# --------------------------------------------------------------------------- #
# Memory directory indexing
# --------------------------------------------------------------------------- #

_TEXT_SUFFIXES = {".txt", ".md", ".rst", ".log", ".json", ".csv", ".py"}


def memory_index(path: str, recursive: bool = True) -> str:
    """Index a directory's text files into long-term memory."""
    runtime = _runtime()
    if runtime is None:
        return _need("memory_index")
    root = Path(path).expanduser()
    if not root.exists():
        return f"Error: path not found: {path}"
    files = [root] if root.is_file() else (
        sorted(root.rglob("*") if recursive else root.glob("*")))
    indexed = 0
    for file_path in files:
        if not file_path.is_file() or file_path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for chunk in _chunks(text):
            runtime.memory.store(
                chunk, kind="document", tags=["indexed"],
                source=str(file_path),
                metadata={"source_path": str(file_path)})
            indexed += 1
    return f"Indexed {indexed} chunks from {path} into memory."


def _chunks(text: str, size: int = 1200) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [text[i:i + size] for i in range(0, len(text), size)][:50]


def memory_retrieve(query: str, top_k: int = 5) -> str:
    """Retrieve memories relevant to a query."""
    runtime = _runtime()
    if runtime is None:
        return _need("memory_retrieve")
    return runtime.memory.context_for(query, top_k=int(top_k)) or \
        "No memories matched."


def memory_manage(action: str, memory_id: int = 0, content: str = "",
                  kind: str = "") -> str:
    """Manage memories: list / delete / count."""
    runtime = _runtime()
    if runtime is None:
        return _need("memory_manage")
    verb = (action or "").lower()
    if verb == "list":
        memories = runtime.memory.list_memories(limit=25, kind=kind or "")
        return "\n".join(f"#{m.id} [{m.kind}] {(m.content or '')[:100]}"
                         for m in memories) or "No memories stored."
    if verb == "delete":
        if not memory_id:
            return "Error: delete needs a memory_id."
        removed = runtime.memory.delete(int(memory_id))
        return f"Deleted memory #{memory_id}." if removed else \
            f"Error: no memory #{memory_id}."
    if verb == "store":
        if not content:
            return "Error: store needs content."
        new_id = runtime.memory.store(content, kind=kind or "fact",
                                      tags=["managed"])
        return f"Stored memory #{new_id}."
    return "Actions: list, delete, store."


# --------------------------------------------------------------------------- #
# Voice / media tools
# --------------------------------------------------------------------------- #

def audio_transcribe(path: str) -> str:
    """Transcribe an audio file with the configured CPU STT engine."""
    runtime = _runtime()
    if runtime is None:
        return _need("audio_transcribe")
    target = Path(path).expanduser()
    if not target.exists():
        return f"Error: audio file not found: {path}"
    from ...voice.stt import build_stt

    vc = runtime.config.voice
    stt = build_stt(
        engine=getattr(vc, "stt_engine", "faster-whisper"),
        model=getattr(vc, "stt_model", "base.en"),
    )
    text = stt.transcribe_file(target)
    return text or "Error: no speech could be transcribed (engine unavailable?)."


def text_to_speech(text: str, path: str = "") -> str:
    """Synthesize speech to a WAV file with the configured CPU TTS engine."""
    runtime = _runtime()
    if runtime is None:
        return _need("text_to_speech")
    from ...voice.tts import build_tts

    vc = runtime.config.voice
    engine = build_tts(
        engine=getattr(vc, "tts_engine", "kokoro"),
        voice=getattr(vc, "tts_voice", "irish_female"),
    )
    out = Path(path).expanduser() if path else \
        Path(runtime.config.data_dir) / "speech" / \
        f"tts_{int(time.time())}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    result = engine.synthesize(text, out)
    if getattr(result, "ok", False):
        return f"Speech written to {getattr(result, 'path', out)}."
    return f"Error: TTS failed: {getattr(result, 'error', 'unknown error')}"


# --------------------------------------------------------------------------- #
# User profile / skills / digest
# --------------------------------------------------------------------------- #

def user_profile_manage(action: str, content: str = "") -> str:
    """Manage user-profile facts: get / set / clear."""
    runtime = _runtime()
    if runtime is None:
        return _need("user_profile_manage")
    verb = (action or "").lower()
    if verb in ("set", "add", "update"):
        if not content:
            return "Error: set needs content."
        profile_id = runtime.memory.store(content, kind="profile",
                                          tags=["profile"])
        return f"Profile updated (memory #{profile_id})."
    if verb == "clear":
        removed = 0
        for memory in runtime.memory.list_memories(limit=100, kind="profile"):
            runtime.memory.delete(memory.id)
            removed += 1
        return f"Cleared {removed} profile facts."
    facts = runtime.memory.list_memories(limit=25, kind="profile")
    if not facts:
        return "No profile facts stored. Use action=set with content."
    return "\n".join(f"- {(m.content or '')[:200]}" for m in facts)


def skill_manage(action: str, name: str = "", inputs: str = "{}") -> str:
    """Manage skills: list / load / run a skill by name."""
    runtime = _runtime()
    if runtime is None:
        return _need("skill_manage")
    store = getattr(runtime, "skills", None)
    if store is None:
        return "Error: the skills catalog is not initialised."
    verb = (action or "").lower()
    if verb == "list":
        names = store.catalog.names
        return f"{len(names)} skills: {', '.join(names)}" if names else \
            "No skills installed."
    if verb == "load":
        skill = store.catalog.get(name)
        if skill is None:
            return f"Error: no skill named '{name}'."
        return skill.prompt_block()
    if verb == "run":
        try:
            values = json.loads(inputs or "{}")
        except Exception:
            return "Error: inputs must be a JSON object."
        from ...skills.executor import SkillExecutor

        return SkillExecutor(get_registry()).run(store, name, values)
    return "Actions: list, load, run."


def digest_collect(since_hours: float = 24.0) -> str:
    """Collect the raw material for a digest: recent memories + activity."""
    runtime = _runtime()
    if runtime is None:
        return _need("digest_collect")
    cutoff = time.time() - float(since_hours) * 3600
    recent = [m for m in runtime.memory.list_memories(limit=100)
              if (m.created_at or 0) >= cutoff]
    lines = [f"Digest material from the last {since_hours:g}h "
             f"({len(recent)} memories):"]
    lines += [f"- [{m.kind}] {(m.content or '')[:180]}" for m in recent[:40]]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def _s(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()

    reg.register("kg_add_entity", kg_add_entity,
                 "Add (or update) an entity in the knowledge graph.",
                 _s({"name": {"type": "string"}, "kind": {"type": "string"},
                     "props": {"type": "string"}}, ["name"]), namespace="knowledge")
    reg.register("kg_add_relation", kg_add_relation,
                 "Add a directed relation between two knowledge-graph entities.",
                 _s({"source": {"type": "string"}, "relation": {"type": "string"},
                     "target": {"type": "string"}}, ["source", "relation", "target"]),
                 namespace="knowledge")
    reg.register("kg_query", kg_query,
                 "Search knowledge-graph entities and relations by text.",
                 _s({"query": {"type": "string"}, "limit": {"type": "integer"}},
                    ["query"]), namespace="knowledge")
    reg.register("kg_neighbors", kg_neighbors,
                 "List the neighbours of a knowledge-graph entity.",
                 _s({"name": {"type": "string"}, "depth": {"type": "integer"}},
                    ["name"]), namespace="knowledge")
    reg.register("memory_index", memory_index,
                 "Index a directory's text files into long-term memory.",
                 _s({"path": {"type": "string"}, "recursive": {"type": "boolean"}},
                    ["path"]), namespace="memory", dangerous=True)
    reg.register("memory_retrieve", memory_retrieve,
                 "Retrieve memories relevant to a query.",
                 _s({"query": {"type": "string"}, "top_k": {"type": "integer"}},
                    ["query"]), namespace="memory")
    reg.register("memory_manage", memory_manage,
                 "Manage memories: list / delete / store.",
                 _s({"action": {"type": "string"}, "memory_id": {"type": "integer"},
                     "content": {"type": "string"}, "kind": {"type": "string"}},
                    ["action"]), namespace="memory")
    reg.register("audio_transcribe", audio_transcribe,
                 "Transcribe an audio file with the configured CPU STT engine.",
                 _s({"path": {"type": "string"}}, ["path"]), namespace="voice")
    reg.register("text_to_speech", text_to_speech,
                 "Synthesize speech to a WAV file with the configured CPU TTS.",
                 _s({"text": {"type": "string"}, "path": {"type": "string"}},
                    ["text"]), namespace="voice")
    reg.register("user_profile_manage", user_profile_manage,
                 "Manage user-profile facts: get / set / clear.",
                 _s({"action": {"type": "string"}, "content": {"type": "string"}},
                    ["action"]), namespace="memory")
    reg.register("skill_manage", skill_manage,
                 "Manage skills: list / load / run.",
                 _s({"action": {"type": "string"}, "name": {"type": "string"},
                     "inputs": {"type": "string"}}, ["action"]), namespace="skills",
                 dangerous=True)
    reg.register("digest_collect", digest_collect,
                 "Collect recent memories as digest source material.",
                 _s({"since_hours": {"type": "number"}}), namespace="digest")
    return reg


register()

__all__ = [
    "audio_transcribe", "digest_collect", "kg_add_entity", "kg_add_relation",
    "kg_neighbors", "kg_query", "memory_index", "memory_manage",
    "memory_retrieve", "register", "skill_manage", "text_to_speech",
    "user_profile_manage",
]
