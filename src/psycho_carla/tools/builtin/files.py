"""
File operation tools.

Sandboxed to a configured root directory (defaults to the user's home) with an
optional allow-list. Every path is resolved and checked before touching disk.

Layer: Pillar 4 (Tools & Memory).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..registry import ToolRegistry, get_registry

# Matches a Windows drive path (C:\... or C:/...) or a UNC path (\\host\share).
_WINDOWS_ABS_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)", re.IGNORECASE)


class FileSandbox:
    """Guards all filesystem access for the file tools."""

    def __init__(
        self,
        root: str | Path | None = None,
        allowed_paths: list[str] | None = None,
        max_read_bytes: int = 2_000_000,
    ) -> None:
        self.root = Path(root).expanduser().resolve() if root else Path.home()
        self.allowed_paths = [Path(p).expanduser().resolve() for p in (allowed_paths or [])]
        self.max_read_bytes = max_read_bytes

    def resolve(self, path: str) -> Path:
        """Resolve a user-supplied path and confirm it stays inside the sandbox."""
        p = Path(path).expanduser()
        # A Windows drive letter or UNC prefix is absolute even when the host
        # is POSIX — treat it as such so it can never be silently relativised
        # into the sandbox root.
        if _WINDOWS_ABS_RE.match(str(path)):
            if not self._allowed(p.resolve()):
                raise PermissionError(
                    f"Path '{p}' is outside the permitted sandbox "
                    f"(root: {self.root})"
                )
            return p.resolve()
        if not p.is_absolute():
            p = self.root / p
        resolved = p.resolve()
        if not self._allowed(resolved):
            raise PermissionError(
                f"Path '{resolved}' is outside the permitted sandbox "
                f"(root: {self.root})"
            )
        return resolved

    def _contains(self, parent: Path, child: Path) -> bool:
        """True when `child` is inside `parent` (cross-platform safe)."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False

    def _allowed(self, resolved: Path) -> bool:
        if self.allowed_paths:
            for ap in self.allowed_paths:
                if self._contains(ap, resolved):
                    return True
            return False
        return self._contains(self.root, resolved)


# The active sandbox — replaced by `configure_file_sandbox` at startup.
_SANDBOX = FileSandbox()


def configure_file_sandbox(
    root: str | Path | None = None,
    allowed_paths: list[str] | None = None,
    max_read_bytes: int = 2_000_000,
) -> FileSandbox:
    global _SANDBOX
    _SANDBOX = FileSandbox(root, allowed_paths, max_read_bytes)
    return _SANDBOX


def get_sandbox() -> FileSandbox:
    return _SANDBOX


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #
def file_read(path: str, max_bytes: int = 0) -> str:
    """
    Read a text file from disk.

    Args:
        path: Path to the file to read. Relative paths resolve against the sandbox root.
        max_bytes: Maximum number of bytes to read. Zero uses the sandbox default.
    """
    limit = max_bytes or _SANDBOX.max_read_bytes
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"Error: file not found: {target}"
    if target.is_dir():
        return f"Error: '{target}' is a directory. Use list_directory."
    data = target.read_bytes()[:limit]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"[binary file, {len(data)} bytes]"
    if len(data) == limit and target.stat().st_size > limit:
        text += f"\n\n[... truncated at {limit} bytes ...]"
    return text


def file_write(path: str, content: str, append: bool = False) -> str:
    """
    Write text to a file, creating parent directories as needed.

    Args:
        path: Destination path.
        content: Text content to write.
        append: When true, append to the file instead of overwriting it.
    """
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with target.open(mode, encoding="utf-8") as fh:
            fh.write(content)
    except Exception as exc:
        return f"Error: could not write {target}: {exc}"
    verb = "Appended to" if append else "Wrote"
    return f"{verb} {target} ({len(content)} chars)"


def file_list(path: str = ".", pattern: str = "*", recursive: bool = False) -> str:
    """
    List files and directories.

    Args:
        path: Directory to list. Defaults to the sandbox root.
        pattern: Glob pattern to filter entries.
        recursive: When true, walk subdirectories.
    """
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"Error: directory not found: {target}"
    if not target.is_dir():
        return f"Error: '{target}' is not a directory."
    try:
        if recursive:
            entries = sorted(p for p in target.rglob(pattern))
        else:
            entries = sorted(p for p in target.glob(pattern))
    except Exception as exc:
        return f"Error: {exc}"
    lines = []
    for p in entries[:500]:
        kind = "DIR " if p.is_dir() else "FILE"
        size = "" if p.is_dir() else f" {p.stat().st_size}b"
        lines.append(f"{kind} {p}{size}")
    if not lines:
        return f"(no matches for '{pattern}' in {target})"
    return "\n".join(lines)


def file_delete(path: str) -> str:
    """
    Delete a file or (recursively) a directory.

    Args:
        path: The file or directory to remove.
    """
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"Error: not found: {target}"
    try:
        if target.is_dir():
            shutil.rmtree(target)
            return f"Removed directory {target}"
        target.unlink()
        return f"Removed file {target}"
    except Exception as exc:
        return f"Error: could not delete {target}: {exc}"


def file_move(source: str, destination: str) -> str:
    """
    Move or rename a file or directory.

    Args:
        source: Path to move.
        destination: New path.
    """
    try:
        src = _SANDBOX.resolve(source)
        dst = _SANDBOX.resolve(destination)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not src.exists():
        return f"Error: not found: {src}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
    except Exception as exc:
        return f"Error: could not move {src} -> {dst}: {exc}"
    return f"Moved {src} -> {dst}"


def file_search(query: str, path: str = ".", max_results: int = 40) -> str:
    """
    Search file contents for a literal string.

    Args:
        query: The literal text to look for.
        path: Root directory to search.
        max_results: Maximum number of matching lines to return.
    """
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not target.is_dir():
        return f"Error: '{target}' is not a directory."
    hits: list[str] = []
    try:
        for p in target.rglob("*"):
            if len(hits) >= max_results:
                break
            if not p.is_file():
                continue
            if p.suffix.lower() in (".db", ".sqlite", ".bin", ".exe", ".dll", ".png",
                                     ".jpg", ".jpeg", ".pdf", ".zip", ".pyc"):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for num, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    hits.append(f"{p}:{num}: {line.strip()[:200]}")
                    if len(hits) >= max_results:
                        break
    except Exception as exc:
        return f"Error: {exc}"
    if not hits:
        return f"No matches for '{query}' under {target}"
    return "\n".join(hits)


def file_info(path: str) -> str:
    """
    Show metadata for a file or directory.

    Args:
        path: The path to inspect.
    """
    try:
        target = _SANDBOX.resolve(path)
    except PermissionError as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"Error: not found: {target}"
    st = target.stat()
    kind = "directory" if target.is_dir() else "file"
    return (
        f"path: {target}\n"
        f"type: {kind}\n"
        f"size: {st.st_size} bytes\n"
        f"modified: {st.st_mtime}\n"
        f"created: {st.st_ctime}"
    )


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
def register(registry: ToolRegistry | None = None) -> ToolRegistry:
    reg = registry or get_registry()
    reg.register(
        "file_read", file_read,
        "Read a text file from the local filesystem.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "File path to read."},
            "max_bytes": {"type": "integer", "description": "Max bytes to read (0 = default)."},
        }, "required": ["path"]},
        namespace="files",
    )
    reg.register(
        "file_write", file_write,
        "Write or append text to a file.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Destination path."},
            "content": {"type": "string", "description": "Content to write."},
            "append": {"type": "boolean", "description": "Append instead of overwrite."},
        }, "required": ["path", "content"]},
        namespace="files",
    )
    reg.register(
        "file_list", file_list,
        "List files and directories.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Directory to list."},
            "pattern": {"type": "string", "description": "Glob filter."},
            "recursive": {"type": "boolean", "description": "Walk subdirectories."},
        }},
        namespace="files",
    )
    reg.register(
        "file_delete", file_delete,
        "Delete a file or directory.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to delete."},
        }, "required": ["path"]},
        namespace="files", dangerous=True,
    )
    reg.register(
        "file_move", file_move,
        "Move or rename a file or directory.",
        {"type": "object", "properties": {
            "source": {"type": "string", "description": "Source path."},
            "destination": {"type": "string", "description": "Destination path."},
        }, "required": ["source", "destination"]},
        namespace="files", dangerous=True,
    )
    reg.register(
        "file_search", file_search,
        "Search file contents for a literal string.",
        {"type": "object", "properties": {
            "query": {"type": "string", "description": "Text to search for."},
            "path": {"type": "string", "description": "Root directory."},
            "max_results": {"type": "integer", "description": "Max matches."},
        }, "required": ["query"]},
        namespace="files",
    )
    reg.register(
        "file_info", file_info,
        "Show metadata for a file or directory.",
        {"type": "object", "properties": {
            "path": {"type": "string", "description": "Path to inspect."},
        }, "required": ["path"]},
        namespace="files",
    )
    return reg


register()
