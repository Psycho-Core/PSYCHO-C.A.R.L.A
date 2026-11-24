---
name: translate-doc
description: "Translate a document from one language to another while preserving formatting"
version: "0.1.0"
author: openjarvis
---

# translate-doc

Translate a document from one language to another while preserving formatting

Inputs: `file_path`, `target_language`

## Steps

1. `file_read` → `original_content`
2. `think` → `analysis`
3. `llm_generate` → `translation`
4. `think` → `reviewed_translation`

Run with: friday skill run translate-doc '{"input": "value"}'
