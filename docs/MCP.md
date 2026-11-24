# MCP Configuration

PSYCHO-C.A.R.L.A. speaks the Model Context Protocol on **both** sides: it
connects to MCP servers as a client, and it can run as an MCP server itself.

---

## As a client — connecting to MCP servers

Declare servers in `psycho_carla.toml`:

```toml
[tools.mcp]
enabled = true
request_timeout = 30

[tools.mcp.servers.filesystem]
transport = "stdio"
command   = "npx"
args      = ["-y", "@modelcontextprotocol/server-filesystem", "D:/notes"]

[tools.mcp.servers.brave-search]
transport = "stdio"
command   = "npx"
args      = ["-y", "@modelcontextprotocol/server-brave-search"]
env        = { BRAVE_API_KEY = "your-key-here" }

[tools.mcp.servers.remote]
transport = "http"
url       = "http://localhost:3000/mcp"
headers    = { Authorization = "Bearer token" }
```

Then:

```bash
carla mcp connect     # start every server and import its tools
carla mcp status      # show connection state
```

Or from Python:

```python
from psycho_carla import Carla

f = Carla()
print(f.runtime.mcp.connect_all())
# {'filesystem': 'ok (6 tools)', 'brave-search': 'ok (2 tools)'}
```

### How tools are named

Each remote tool is bridged into the local registry as:

```
mcp__<server_name>__<tool_name>
```

So `mcp__filesystem__read_file` and `mcp__brave-search__brave_web_search`
become ordinary callable tools the model can select like any native one.

Prefixes are configurable:

```toml
[tools.mcp.servers.filesystem]
prefix = "fs__"      # -> fs__read_file
```

### Transports

| Transport | Config | Notes |
|-----------|--------|-------|
| `stdio` | `command` + `args` | The default. The server runs as a subprocess. |
| `http` | `url` + optional `headers` | Streamable HTTP JSON-RPC. |
| `sse` | `url` | Treated as HTTP with SSE-compatible framing. |

### Environment variables in server configs

`env` values are expanded with `os.path.expandvars`, so you can keep secrets
out of the TOML:

```toml
[tools.mcp.servers.brave-search]
env = { BRAVE_API_KEY = "%BRAVE_API_KEY%" }
```

---

## As a server — exposing C.A.R.L.A.

C.A.R.L.A. can expose its own capabilities over MCP, so any MCP client can
drive it.

```bash
carla mcp serve
```

This starts a JSON-RPC 2.0 server on stdio.

### Exposed tools

| Tool | Purpose |
|------|---------|
| `carla_ask` | Send a prompt to the runtime. Args: `prompt`, `agent`, `tier` |
| `carla_memory_search` | Semantic search over the memory store. Args: `query`, `top_k` |
| `carla_memory_store` | Store a memory. Args: `content`, `tags` |
| `carla_list_agents` | List every available agent |
| `carla_telemetry` | Current efficiency telemetry summary |

### Claude Desktop

Add to `claude_desktop_config.json`:

**Windows:**
```
%APPDATA%\Claude\claude_desktop_config.json
```

**macOS:**
```
~/Library/Application Support/Claude/claude_desktop_config.json
```

```json
{
  "mcpServers": {
    "carla": {
      "command": "C:\\path\\to\\venv\\Scripts\\carla.exe",
      "args": ["mcp", "serve"]
    }
  }
}
```

Or, without installing the console script:

```json
{
  "mcpServers": {
    "carla": {
      "command": "C:\\path\\to\\venv\\Scripts\\python.exe",
      "args": ["-m", "psycho_carla.cli.main", "mcp", "serve"]
    }
  }
}
```

### Programmatic

```python
from psycho_carla import Carla
from psycho_carla.tools.mcp.server import build_default_server

f = Carla()
server = build_default_server(f.runtime)
server.serve_stdio()      # blocks on stdin
```

---

## Writing your own MCP server

Any MCP server works. The protocol is simple — implement four methods:

```
initialize           -> return protocolVersion, capabilities, serverInfo
tools/list           -> return [{"name", "description", "inputSchema"}]
tools/call           -> execute, return {"content": [{"type": "text", "text": ...}]}
notifications/initialized  -> (no response)
```

A minimal Python server:

```python
import json, sys

TOOLS = [{
    "name": "get_weather",
    "description": "Get the weather for a city.",
    "inputSchema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}]

def handle(msg):
    method = msg.get("method")
    if method == "initialize":
        return {"protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "weather", "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        city = msg["params"]["arguments"]["city"]
        return {"content": [{"type": "text", "text": f"It is 18°C in {city}."}]}
    return {}

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    if "id" in msg:
        result = handle(msg)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}),
              flush=True)
```

Register it:

```toml
[tools.mcp.servers.weather]
transport = "stdio"
command   = "python"
args      = ["C:/scripts/weather_server.py"]
```

---

## Troubleshooting

**Server doesn't start.** Check `carla mcp connect` output. Common causes:
the command isn't on PATH (use an absolute path), or the package isn't installed
in the environment C.A.R.L.A. runs in.

**Tools don't appear.** Confirm the server started *before* listing — the
manager calls `initialize` then `tools/list` at connect time. Tools imported
from MCP have namespace `mcp` in the HUD's Tool Matrix.

**Timeouts.** Raise `[tools.mcp].request_timeout`. Some servers take a while to
load their model on first request.

**A tool errors.** Check the server's stderr — C.A.R.L.A. surfaces the error
text in the tool result, which appears in the HUD's tool-call pills and in the
conversation transcript.
