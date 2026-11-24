---
name: security-scan
description: "Scan source code for common security vulnerabilities and report findings"
version: "0.1.0"
author: openjarvis
---

# security-scan

Scan source code for common security vulnerabilities and report findings

Inputs: `file_path`

## Steps

1. `file_read` → `source_code`
2. `think` → `vulnerabilities`
3. `think` → `report`

Run with: friday skill run security-scan '{"input": "value"}'
