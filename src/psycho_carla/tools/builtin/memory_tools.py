"""
Memory and scheduler tools.

These are wired to the live runtime rather than being free functions, so the
`memory_curator` and `scheduler` agents can act on real state. The runtime
installs them at start-up via `install_runtime_tools`.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

from typing import Any

from ..registry import ToolRegistry


def install_runtime_tools(registry: ToolRegistry, runtime: Any) -> ToolRegistry:
    """Register the tools that need a live runtime handle."""

    # --- memory ---------------------------------------------------------- #
    def memory_search(query: str, top_k: int = 5) -> str:
        """
        Search the persistent memory store semantically.

        Args:
            query: What to look for.
            top_k: Maximum number of memories to return.
        """
        hits = runtime.memory.search(query, top_k=top_k)
        if not hits:
            return "No matching memories."
        return "\n".join(f"[{m.id}] {m.content}" for m in hits)

    def memory_store(content: str, tags: str = "") -> str:
        """
        Store a new memory in the persistent store.

        Args:
            content: The fact or note to remember.
            tags: Comma-separated tags for later retrieval.
        """
        tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]
        mem_id = runtime.memory.store(content, tags=tag_list, source="agent")
        return f"Stored memory {mem_id}."

    def memory_delete(memory_id: int) -> str:
        """
        Delete a memory by its numeric id.

        Args:
            memory_id: The id of the memory to delete.
        """
        ok = runtime.memory.delete(int(memory_id))
        return f"Deleted memory {memory_id}." if ok else f"No memory {memory_id}."

    def memory_list(limit: int = 50) -> str:
        """
        List the most recently stored memories.

        Args:
            limit: Maximum number of memories to list.
        """
        items = runtime.memory.list_memories(limit=limit)
        if not items:
            return "The memory store is empty."
        return "\n".join(f"[{m.id}] ({m.kind}) {m.content}" for m in items)

    def memory_get(memory_id: int) -> str:
        """
        Fetch one memory by its numeric id.

        Args:
            memory_id: The id of the memory to fetch.
        """
        m = runtime.memory.get(int(memory_id))
        if m is None:
            return f"No memory {memory_id}."
        return f"[{m.id}] ({m.kind}) {m.content}"

    # --- cron ------------------------------------------------------------- #
    def cron_list() -> str:
        """List all scheduled jobs."""
        jobs = runtime.list_jobs()
        if not jobs:
            return "No jobs scheduled."
        return "\n".join(
            f"[{j['id']}] {j['schedule']} — {j['name']} ({j['agent']}) "
            f"last: {j['last_status'] or 'never run'}"
            for j in jobs
        )

    def cron_add(
        name: str,
        schedule: str,
        agent: str = "morning_digest",
        prompt: str = "",
    ) -> str:
        """
        Schedule a recurring job using a 5-field cron expression.

        Args:
            name: A short name for the job.
            schedule: Cron expression: minute hour day-of-month month day-of-week.
            agent: Which agent should run the job.
            prompt: The instruction given to the agent on each run.
        """
        try:
            job = runtime.add_job(
                name=name, schedule=schedule, agent=agent,
                prompt=prompt or f"Run the {name} scheduled task.",
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return f"Scheduled job {job.id} ({schedule})."

    def cron_remove(job_id: str) -> str:
        """
        Remove a scheduled job.

        Args:
            job_id: The id of the job to remove.
        """
        ok = runtime.remove_job(job_id)
        return f"Removed job {job_id}." if ok else f"No job {job_id}."

    def cron_run_now(job_id: str) -> str:
        """
        Trigger a scheduled job immediately.

        Args:
            job_id: The id of the job to run.
        """
        ok = runtime.scheduler.run_now(job_id)
        return f"Dispatched job {job_id}." if ok else f"No job {job_id}."

    registry.register(
        "memory_search", memory_search,
        "Search the persistent memory store semantically.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for."},
            "top_k": {"type": "integer", "description": "Maximum results."},
        }, "required": ["query"]},
        namespace="memory",
    )
    registry.register(
        "memory_store", memory_store,
        "Store a fact or note in the persistent memory store.",
        {"type": "object", "properties": {
            "content": {"type": "string", "description": "What to remember."},
            "tags": {"type": "string", "description": "Comma-separated tags."},
        }, "required": ["content"]},
        namespace="memory",
    )
    registry.register(
        "memory_delete", memory_delete,
        "Delete a memory by id.",
        {"type": "object", "properties": {
            "memory_id": {"type": "integer", "description": "Memory id."},
        }, "required": ["memory_id"]},
        namespace="memory", dangerous=True,
    )
    registry.register(
        "memory_get", memory_get,
        "Fetch one memory by id.",
        {"type": "object", "properties": {
            "memory_id": {"type": "integer", "description": "Memory id."},
        }, "required": ["memory_id"]},
        namespace="memory",
    )
    registry.register(
        "memory_list", memory_list,
        "List recently stored memories.",
        {"type": "object", "properties": {
            "limit": {"type": "integer", "description": "Maximum entries."},
        }},
        namespace="memory",
    )
    registry.register(
        "cron_list", cron_list,
        "List all scheduled jobs.",
        {"type": "object", "properties": {}},
        namespace="cron",
    )
    registry.register(
        "cron_add", cron_add,
        "Schedule a recurring job with a cron expression.",
        {"type": "object", "properties": {
            "name": {"type": "string", "description": "Job name."},
            "schedule": {"type": "string", "description": "Cron expression."},
            "agent": {"type": "string", "description": "Agent to run."},
            "prompt": {"type": "string", "description": "Instruction for each run."},
        }, "required": ["name", "schedule"]},
        namespace="cron",
    )
    registry.register(
        "cron_remove", cron_remove,
        "Remove a scheduled job.",
        {"type": "object", "properties": {
            "job_id": {"type": "string", "description": "Job id."},
        }, "required": ["job_id"]},
        namespace="cron", dangerous=True,
    )
    registry.register(
        "cron_run_now", cron_run_now,
        "Trigger a scheduled job immediately.",
        {"type": "object", "properties": {
            "job_id": {"type": "string", "description": "Job id."},
        }, "required": ["job_id"]},
        namespace="cron",
    )
    return registry
