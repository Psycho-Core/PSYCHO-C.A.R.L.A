---
name: meeting-notes
description: "Generate structured meeting notes from a transcript or raw notes"
version: "0.1.0"
author: openjarvis
---

# meeting-notes

Generate structured meeting notes from a transcript or raw notes

Inputs: `transcript_path`

## Steps

1. `file_read` → `transcript`
2. `think` → `extracted_info`
3. `think` → `meeting_notes`

Run with: friday skill run meeting-notes '{"input": "value"}'
