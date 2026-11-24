---
name: web-summarize
description: "Fetch a web page and summarize its content into key points"
version: "0.1.0"
author: openjarvis
---

# web-summarize

Fetch a web page and summarize its content into key points

Inputs: `url`

## Steps

1. `http_request` → `page_content`
2. `think` → `clean_content`
3. `think` → `summary`

Run with: friday skill run web-summarize '{"input": "value"}'
