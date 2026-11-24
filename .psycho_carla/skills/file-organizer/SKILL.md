---
name: file-organizer
description: "Organize files in a directory by type (images, documents, code, etc.)"
version: "0.1.0"
author: openjarvis
---

# file-organizer

Organize files in a directory by type (images, documents, code, etc.)

Inputs: `directory`

## Steps

1. `shell_exec` → `file_listing`
2. `think` → `organization_plan`
3. `shell_exec` → `dirs_created`
4. `think` → `report`

Run with: friday skill run file-organizer '{"input": "value"}'
