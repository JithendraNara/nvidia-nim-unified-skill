---
name: nvidia-nim-unified
description: Use when a task needs NVIDIA NIM OCR, page elements, table structure, graphic element detection, or passage reranking through one shared routing layer. Selects the right capability or multi-step workflow from a free-form query, prepares requests, and can invoke the configured endpoint.
short-description: Unified NVIDIA NIM router for OCR, layout, and reranking
aliases: [nim, nvidia-nim]
trigger_keywords: [extract text, OCR, document layout, page elements, table structure, table cells, chart labels, rerank passages, detect layout, image to text, pdf text extraction, passage ranking, search relevance]
metadata: {openclaw: {emoji: "🟩", homepage: "https://github.com/JithendraNara/nvidia-nim-unified-skill", requires: {anyBins: [python3, python]}}}
---

# NVIDIA NIM Unified

This skill wraps five NVIDIA NIM capabilities behind one routing layer:

- `ocr`
- `page_elements`
- `table_structure`
- `graphic_elements`
- `rerank`

Do not pretend these are one physical OpenAPI endpoint. They are different capabilities with different request shapes. Treat this skill as the abstraction layer that hides those differences from the agent.

## Invocation Patterns

### Claude Code
Explicit invocation:
```
/nvidia-nim
/nvidia-nim plan --task-query "extract text from invoice"
/nim invoke --capability ocr --image-url https://example.com/doc.png
```

Implicit trigger: Claude Code activates this skill when descriptions contain keywords like "extract text", "OCR", "image to text", "pdf text", "document layout", "table structure", "rerank", "passage ranking", "detect chart", "page elements".

### Codex (OpenAI)
Explicit invocation:
```
$nvidia-nim
$nvidia-nim-unified
$nvidia-nim plan --task-query "extract text from invoice"
$nvidia-nim build-request --capability ocr --image-url https://example.com/doc.png
```

Implicit trigger: Codex activates this skill when queries mention OCR, layout detection, table extraction, chart understanding, or passage reranking.

### OpenClaw
The skill loads automatically when the task description matches trigger keywords. For explicit invocation, reference the skill by name `nvidia-nim-unified` or `nim`.

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

Build a request without sending it. For the four CV capabilities, the router accepts an `https://` URL, a local image path, or an existing `data:image/...` URL and normalizes it to the base64 data-URL format that the managed NVIDIA endpoint expects.

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

Use `--config {baseDir}/references/nvidia-nim-config.example.json` only after copying that file and replacing example values with real endpoints or env names. By default, the bundled config targets NVIDIA's managed `ai.api.nvidia.com` endpoints for all five capabilities.

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

- all five managed endpoints: `NVIDIA_API_KEY`
- self-hosted overrides: optional per-capability URL override plus bearer env if you are not using the managed API

Default endpoint env names:

- `NVIDIA_NIM_OCR_URL`
- `NVIDIA_NIM_PAGE_ELEMENTS_URL`
- `NVIDIA_NIM_TABLE_STRUCTURE_URL`
- `NVIDIA_NIM_GRAPHIC_ELEMENTS_URL`

These may be overridden in the shared config JSON.

## Examples

### Quick Start

Plan a task from a free-form query:

```bash
python3 {baseDir}/scripts/nim_router.py plan --task-query "extract text from invoice"
```

Build and invoke in one step (with API key configured):

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/invoice.png"
```

### Capability Examples

#### OCR (Text Extraction)

Extract text from an image:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/receipt.jpg"
```

Build request without invoking:

```bash
python3 {baseDir}/scripts/nim_router.py build-request \
  --capability ocr \
  --image-url "https://example.com/document.png"
```

#### Page Elements (Layout Detection)

Detect page layout, headers, paragraphs, sections:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability page_elements \
  --image-url "https://example.com/page.png"
```

#### Table Structure

Detect table cells, rows, columns, headers:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability table_structure \
  --image-url "https://example.com/spreadsheet.png"
```

#### Graphic Elements (Chart Detection)

Detect chart labels, axes, legends, titles:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability graphic_elements \
  --image-url "https://example.com/chart.png"
```

#### Rerank (Passage Ranking)

Rank passages by relevance to a query:

```bash
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability rerank \
  --query-text "Which GPU has the fastest memory?" \
  --passage "A100: 2 TB/s memory bandwidth" \
  --passage "H100: 3.35 TB/s memory bandwidth" \
  --passage "RTX 4090: 1 TB/s memory bandwidth"
```

### Workflow Examples

#### OCR + Rerank (ocr_then_rerank)

Use when task combines text extraction and relevance ranking:

```bash
# Step 1: Plan the workflow
python3 {baseDir}/scripts/nim_router.py plan \
  --task-query "extract text from image and rank passages by relevance" \
  --image-url "https://example.com/doc.png" \
  --query-text "H100 specifications"

# Step 2: Run OCR first
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/doc.png"

# Step 3: Use extracted text as passages for reranking
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability rerank \
  --query-text "H100 specifications" \
  --passage "First passage from OCR result..." \
  --passage "Second passage from OCR result..."
```

#### Layout-Aware Table Extraction (page_elements → table_structure → ocr)

Use when extracting text while preserving table structure:

```bash
# Step 1: Detect page layout
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability page_elements \
  --image-url "https://example.com/report.png"

# Step 2: Detect table structure
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability table_structure \
  --image-url "https://example.com/report.png"

# Step 3: Extract text with layout context
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/report.png"
```

#### Chart-Aware Extraction (page_elements → graphic_elements → ocr)

Use when understanding charts while reading surrounding content:

```bash
# Step 1: Detect page layout
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability page_elements \
  --image-url "https://example.com/dashboard.png"

# Step 2: Detect chart elements
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability graphic_elements \
  --image-url "https://example.com/dashboard.png"

# Step 3: Extract text with chart context
python3 {baseDir}/scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/dashboard.png"
```

### Platform-Specific Invocation

#### Claude Code

Explicit invocation:
```
/nvidia-nim
/nvidia-nim invoke --capability ocr --image-url https://example.com/doc.png
```

Implicit trigger: Skill activates when descriptions contain "extract text", "OCR", "document layout", "table structure", "rerank", "chart labels".

#### Codex (OpenAI)

Explicit invocation:
```
$nvidia-nim
$nvidia-nim invoke --capability ocr --image-url https://example.com/doc.png
```

Implicit trigger: Skill activates for queries mentioning OCR, layout detection, table extraction, chart understanding, or passage reranking.

#### OpenClaw

The skill loads automatically when task description matches trigger keywords. For explicit invocation, reference `nvidia-nim-unified` or `nim`.

### Image Source Formats

The router accepts three image source formats and converts to base64 data URLs:

```bash
# HTTPS URL (auto-downloaded and converted)
--image-url "https://example.com/image.png"

# Local file path (read and converted)
--image-url "/path/to/local/image.jpg"

# Data URL (passed through directly)
--image-url "data:image/png;base64,iVBORw0KG..."
```

## Constraints

- The router is deterministic and rule-based. If the task is ambiguous, inspect the returned rationale instead of blindly invoking.
- Multi-step workflows are planned automatically, but the script invokes only one capability per call.
- the default bundle targets NVIDIA managed APIs for all five capabilities.
- managed CV endpoints rejected plain remote image URLs in live testing; the router converts image sources to base64 data URLs before invocation.
