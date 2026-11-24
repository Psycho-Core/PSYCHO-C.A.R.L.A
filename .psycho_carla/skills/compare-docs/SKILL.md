---
name: compare-docs
description: "Compare two documents and summarize their differences"
version: "0.1.0"
author: openjarvis
---

# compare-docs

Compare two documents and summarize their differences

Inputs: `file_a`, `file_b`

## Steps

1. `file_read` → `doc_a`
2. `file_read` → `doc_b`
3. `think` → `differences`
4. `think` → `comparison_report`

Run with: friday skill run compare-docs '{"input": "value"}'
