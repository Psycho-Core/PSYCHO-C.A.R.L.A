---
name: pdf-summarize
description: "Extract text from a PDF document and generate a structured summary"
version: "0.1.0"
author: openjarvis
---

# pdf-summarize

Extract text from a PDF document and generate a structured summary

Inputs: `pdf_path`

## Steps

1. `pdf_extract` → `pdf_text`
2. `think` → `structure`
3. `think` → `summary`

Run with: friday skill run pdf-summarize '{"input": "value"}'
