---
name: backup-files
description: "Create a timestamped backup archive of specified files or directory"
version: "0.1.0"
author: openjarvis
---

# backup-files

Create a timestamped backup archive of specified files or directory

Inputs: `destination`, `source`

## Steps

1. `shell_exec` → `timestamp`
2. `shell_exec` → `archive_result`
3. `think` → `report`

Run with: friday skill run backup-files '{"input": "value"}'
