---
name: search-and-index
description: "Search for documents matching a query and index them into memory for future retrieval"
version: "0.1.0"
author: openjarvis
---

# search-and-index

Search for documents matching a query and index them into memory for future retrieval

Inputs: `directory`, `query`

## Steps

1. `shell_exec` → `found_files`
2. `think` → `relevant_files`
3. `memory_index` → `index_result`
4. `memory_search` → `search_results`

Run with: friday skill run search-and-index '{"input": "value"}'
