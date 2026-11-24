---
name: data-analyze
description: "Analyze a data file and generate statistical insights and visualizations"
version: "0.1.0"
author: openjarvis
---

# data-analyze

Analyze a data file and generate statistical insights and visualizations

Inputs: `data_path`

## Steps

1. `file_read` → `raw_data`
2. `think` → `data_stats`
3. `think` → `analysis`
4. `think` → `report`

Run with: friday skill run data-analyze '{"input": "value"}'
