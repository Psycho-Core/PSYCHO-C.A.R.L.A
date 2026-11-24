---
name: code-lint
description: "Read a source file, analyze it for code quality issues, and report findings"
version: "0.1.0"
author: openjarvis
---

# code-lint

Read a source file, analyze it for code quality issues, and report findings

Inputs: `file_path`

## Steps

1. `file_read` → `source_code`
2. `think` → `issues`
3. `think` → `report`

Run with: friday skill run code-lint '{"input": "value"}'
