---
name: file-deduplicator
description: "Find and report duplicate files in a directory by comparing checksums"
version: "0.1.0"
author: openjarvis
---

# file-deduplicator

Find and report duplicate files in a directory by comparing checksums

Inputs: `directory`

## Steps

1. `shell_exec` → `checksums`
2. `think` → `duplicates`
3. `think` → `report`

Run with: friday skill run file-deduplicator '{"input": "value"}'
