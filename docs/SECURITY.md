# Security model

C.A.R.L.A. has shell, file and web tools, so it is built to assume that **text it reads, web
pages you visit, and models it talks to may be hostile**. What protects you, and what does not:

## Protected (tested)

| Threat | Defence |
|---|---|
| A website you visit calls the local server (`127.0.0.1:8787`) to run shell commands or read your memory | **Origin allow-list**: only the HUD (localhost origins) and origins you list in `[server] cors_origins` can call the API from a browser. A `"*"` entry is ignored on a localhost bind. |
| Cross-site POSTs that need no CORS preflight (`text/plain`) | **Cross-site write guard**: POST/PUT/PATCH/DELETE with a foreign `Origin` header is refused (403). |
| DNS rebinding (a hostile domain re-pointed at 127.0.0.1) | **Host check**: on a localhost bind the `Host` header must be a loopback name. |
| Cross-site WebSocket hijacking | The `/ws` endpoint applies the same origin and host checks. |
| An agent reading hostile web content is told to switch safe mode off through C.A.R.L.A.'s own API | `http_request` / `web_fetch` / the browser tools **refuse loopback, private-network and link-local targets** (the local server, your router, cloud metadata), validate **every redirect hop**, and allow only `http(s)`/`data:`. |
| An agent edits files / runs code without you knowing | **Safe mode** (default on): shell, code, file write/delete/move, patch and commit tools wait in the **Approvals** panel. An agent cannot approve its own request. |
| Exposing the server on your network | A non-localhost bind **refuses to start without an API key**. |
| Malicious backup / archive files | Restore only writes an allow-list of file names, blocks path traversal, refuses zip bombs, keeps your old files aside and rolls back if a file is locked. Engine archives are extracted with path checks. |

## Not protected / know this

- **The vault's key sits next to the vault file** (`vault.enc` + `vault.key`). It stops casual
  reading of API keys, not someone with access to your user folder. Backups exclude both by default.
- **Anything running as you on your PC** can call the local server (curl, other programs) — the
  guards above are about *web pages* and *agents*, not local malware.
- **MCP servers you connect** bring their own tools, which are not covered by safe mode.
- **Models**: an "abliterated" model has refusals removed; safe mode and the SSRF rules still apply
  to what it *does*, but not to what it *says*.
- **Downloads**: models come from Hugging Face repos you choose (no revision pinning). GGUF files are
  data, but a malicious file could still target bugs in llama.cpp — download from uploaders you trust.
- The Windows exe is **not code-signed**.

## Reporting

Open an issue on the GitHub repository (or contact the owner privately for anything serious).
