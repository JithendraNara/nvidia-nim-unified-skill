---
name: nvidia-nim-unified
description: Use when a task needs NVIDIA NIM OCR, page elements, table structure, graphic element detection, or passage reranking through one shared routing layer. Selects the right capability or multi-step workflow from a free-form query, prepares requests, and can invoke the configured endpoint.
metadata: {"openclaw":{"emoji":"🟩","homepage":"https://github.com/JithendraNara/nvidia-nim-unified-skill","requires":{"anyBins":["python3","python"]}},"short-description":"Unified NVIDIA NIM router for OCR, layout, and reranking"}
---

# NVIDIA NIM Unified

This skill wraps five NVIDIA NIM capabilities behind one routing layer:

- `ocr`
- `page_elements`
- `table_structure`
- `graphic_elements`
- `rerank`

Do not pretend these are one physical OpenAPI endpoint. They are different capabilities with different request shapes. Treat this skill as the abstraction layer that hides those differences from the agent.

## Use This Skill When

- The user asks to extract text from images or documents.
- The user asks to detect document layout, page sections, charts, tables, or headers.
- The user asks to keep table or chart structure while processing an image.
- The user asks to rerank passages against a query.
- The user wants one shared NVIDIA workflow that Codex, Claude, and OpenClaw can all call.

## Files

- `{baseDir}/references/nim-capabilities.json`: normalized machine-readable catalog of capabilities, inputs, response shapes, routing hints, and source specs.
- `{baseDir}/references/openapi/*.yaml`: vendored source OpenAPI specs for all supported capabilities.
- `{baseDir}/references/nvidia-nim-config.example.json`: shared config template for endpoint URLs and auth env names.
- `{baseDir}/scripts/nim_router.py`: planner, request builder, and optional HTTP invoker.
- `nvidia-nim-unified-skill.yaml`: lightweight manifest for non-Codex consumers.

## Core Workflow

1. Start with a natural-language task query.
2. Run the planner:

```bash
python3 {baseDir}/scripts/nim_router.py plan \
  --task-query "extract text from an invoice image and preserve table structure"
```

3. If the plan returns a single capability, build or invoke that request directly.
4. If the plan returns a workflow, execute the listed capabilities in sequence.
5. For exact payload or routing details, read `{baseDir}/references/nim-capabilities.json`.

## Request Building

Build a request without sending it:

```bash
python3 {baseDir}/scripts/nim_router.py build-request \
  --capability ocr \
  --image-url "https://example.com/invoice.png"
```

Rerank example:

```bash
python3 {baseDir}/scripts/nim_router.py build-request \
  --capability rerank \
  --query-text "Which chunk mentions H100 memory bandwidth?" \
  --passage "A100 reaches over 2 TB/s." \
  --passage "H100 offers 3 TB/s of memory bandwidth per GPU."
```

## Invocation

If endpoints and auth are configured, the same script can invoke the request:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability page_elements \
  --image-url "https://example.com/page.png"
```

Use `--config {baseDir}/references/nvidia-nim-config.example.json` only after copying that file and replacing example values with real endpoints or env names.

## Routing Rules

- Text extraction only: prefer `ocr`
- Page layout detection: prefer `page_elements`
- Table cell, row, column, or header detection: prefer `table_structure`
- Chart labels, axes, legends, or marks: prefer `graphic_elements`
- Search relevance or document chunk ranking: prefer `rerank`
- OCR plus ranking: use workflow `ocr -> rerank`
- OCR plus tables: use workflow `page_elements -> table_structure -> ocr`
- OCR plus chart understanding: use workflow `page_elements -> graphic_elements -> ocr`

## Configuration

Default auth expectations:

- `rerank`: requires `NVIDIA_API_KEY`
- self-hosted infer services: optional `NVIDIA_NIM_BEARER_TOKEN`

Default endpoint env names:

- `NVIDIA_NIM_OCR_URL`
- `NVIDIA_NIM_PAGE_ELEMENTS_URL`
- `NVIDIA_NIM_TABLE_STRUCTURE_URL`
- `NVIDIA_NIM_GRAPHIC_ELEMENTS_URL`

These may be overridden in the shared config JSON.

## Constraints

- The router is deterministic and rule-based. If the task is ambiguous, inspect the returned rationale instead of blindly invoking.
- Multi-step workflows are planned automatically, but the script invokes only one capability per call.
- `rerank` is a managed NVIDIA API path. The other four capabilities are modeled as self-hosted NIM endpoints unless your config points them elsewhere.
