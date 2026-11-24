---
name: todo-from-notes
description: "Extract actionable todo items from unstructured notes"
version: "0.1.0"
author: openjarvis
---

# todo-from-notes

Extract actionable todo items from unstructured notes

Inputs: `notes_path`

## Steps

1. `file_read` → `notes_content`
2. `think` → `extracted_todos`
3. `think` → `todo_list`

Run with: friday skill run todo-from-notes '{"input": "value"}'
