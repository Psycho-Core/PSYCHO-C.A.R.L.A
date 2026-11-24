---
name: topic-research
description: "Research a topic using web search, check memory for prior knowledge, and synthesize findings"
version: "0.1.0"
author: openjarvis
---

# topic-research

Research a topic using web search, check memory for prior knowledge, and synthesize findings

Inputs: `topic`

## Steps

1. `memory_search` → `prior_knowledge`
2. `web_search` → `search_results`
3. `think` → `synthesis`
4. `memory_store` → `stored`

Run with: friday skill run topic-research '{"input": "value"}'
