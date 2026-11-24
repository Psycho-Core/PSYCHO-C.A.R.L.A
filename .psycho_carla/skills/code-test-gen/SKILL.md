---
name: code-test-gen
description: "Read a source file and generate unit tests for its functions and classes"
version: "0.1.0"
author: openjarvis
---

# code-test-gen

Read a source file and generate unit tests for its functions and classes

Inputs: `file_path`

## Steps

1. `file_read` → `source_code`
2. `think` → `analysis`
3. `code_interpreter` → `generated_tests`
4. `think` → `review`

Run with: friday skill run code-test-gen '{"input": "value"}'
