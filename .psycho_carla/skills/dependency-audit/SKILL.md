---
name: dependency-audit
description: "Check project dependencies for known vulnerabilities and outdated packages"
version: "0.1.0"
author: openjarvis
---

# dependency-audit

Check project dependencies for known vulnerabilities and outdated packages

Inputs: `project_path`

## Steps

1. `file_read` → `project_config`
2. `think` → `dependencies`
3. `web_search` → `advisories`
4. `think` → `report`

Run with: friday skill run dependency-audit '{"input": "value"}'
