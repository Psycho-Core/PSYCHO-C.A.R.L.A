---
name: knowledge-extract
description: "Extract structured knowledge from a document and store in memory"
version: "0.1.0"
author: openjarvis
---

# knowledge-extract

Extract structured knowledge from a document and store in memory

Inputs: `file_path`

## Steps

1. `file_read` → `document_content`
2. `think` → `extracted_knowledge`
3. `memory_store` → `stored`

Run with: friday skill run knowledge-extract '{"input": "value"}'
