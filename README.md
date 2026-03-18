# NVIDIA NIM Unified Skill

A cross-platform AI agent skill for routing tasks to NVIDIA NIM (NVIDIA Inference Microservices) capabilities. Works with **OpenClaw**, **Claude Code**, and **Codex**.

## What It Does

Given a natural language task like *"extract text from this invoice"* or *"find the relevant passages"*, this skill automatically:

1. **Plans** - Routes to the correct NVIDIA NIM capability
2. **Builds** - Constructs the proper API request
3. **Invokes** - Executes against NVIDIA endpoints

## Supported Capabilities

| Capability | What It Does | Example Use Case |
|-----------|--------------|------------------|
| **OCR** | Extract text from images/PDFs | "Read the text from this receipt" |
| **Page Elements** | Detect layout sections (headers, paragraphs, tables, charts) | "What are the main sections of this document?" |
| **Table Structure** | Detect table cells, rows, columns, borders | "Extract the table data preserving structure" |
| **Graphic Elements** | Detect chart labels, axes, legends | "Find all chart labels in this image" |
| **Rerank** | Rank text passages by relevance to a query | "Which chunks mention H100 memory?" |

## Supported Platforms

| Platform | How to Install | How to Invoke |
|---------|---------------|---------------|
| **OpenClaw** | Clone to `~/.openclaw/skills/` | `skill invoke nvidia-nim-unified` |
| **Claude Code** | Clone to `~/.claude/skills/` | `/nvidia-nim` or `/nim` |
| **Codex** | Clone to `~/.agents/skills/` | `$nvidia-nim` or `$nim` |

## Installation

### 1. Clone the Repository

```bash
# Choose your platform's skills directory:

# For OpenClaw
git clone https://github.com/JithendraNara/nvidia-nim-unified-skill.git \
  ~/.openclaw/skills/nvidia-nim-unified

# For Claude Code  
git clone https://github.com/JithendraNara/nvidia-nim-unified-skill.git \
  ~/.claude/skills/nvidia-nim-unified

# For Codex
git clone https://github.com/JithendraNara/nvidia-nim-unified-skill.git \
  ~/.agents/skills/nvidia-nim-unified
```

### 2. Set Up NVIDIA API Key

```bash
export NVIDIA_API_KEY="nvapi-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

Or add it to your shell profile (`~/.bashrc`, `~/.zshrc`).

### 3. (Optional) Configure Custom Endpoints

If using self-hosted NIM endpoints instead of NVIDIA's managed API:

```bash
cp references/nvidia-nim-config.example.json references/nvidia-nim-config.json
# Edit nvidia-nim-config.json with your endpoints
```

## Usage

### Agent Implicit Invocation

The skill activates automatically when relevant tasks are detected:

> "Can you extract the text from this invoice image?"
> "Read this document and tell me what tables it contains"
> "Rank these passages by relevance to GPU performance"

### Agent Explicit Invocation

```bash
# Claude Code
/nvidia-nim extract text from invoice.jpg

# Codex
$nvidia-nim extract text from invoice.jpg

# OpenClaw
skill invoke nvidia-nim-unified extract text from invoice.jpg
```

### Direct CLI Usage

```bash
cd /path/to/nvidia-nim-unified-skill

# Plan: See which capability will be used
python3 scripts/nim_router.py plan \
  --task-query "extract text from invoice"

# Build: Generate the API request JSON
python3 scripts/nim_router.py build-request \
  --capability ocr \
  --image-url "https://example.com/invoice.png"

# Invoke: Execute and get results
python3 scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/invoice.png"
```

## Examples

### OCR: Extract Text from Image

```bash
python3 scripts/nim_router.py invoke \
  --capability ocr \
  --image-url "https://example.com/document.jpg"
```

**Input:** Image URL or local file path  
**Output:** Extracted text with bounding boxes

### Rerank: Find Relevant Passages

```bash
python3 scripts/nim_router.py build-request \
  --capability rerank \
  --query-text "Which GPU has the most memory bandwidth?" \
  --passage "A100: 2 TB/s memory bandwidth" \
  --passage "H100: 3.35 TB/s memory bandwidth" \
  --passage "L40S: 1.5 TB/s memory bandwidth"
```

**Output:** Ranked passages with relevance scores

### Workflows

The router automatically detects when to chain capabilities:

| Task | Workflow |
|------|----------|
| "Extract text and rank by relevance" | `ocr → rerank` |
| "Extract tables from document" | `page_elements → table_structure → ocr` |
| "Understand charts in document" | `page_elements → graphic_elements → ocr` |

## Rate Limits

Default rate limits (matches NVIDIA API):

- **Global default:** 40 requests/minute
- **OCR:** 20 requests/minute  
- **Rerank:** 80 requests/minute

Override in config:

```json
{
  "rate_limit": {
    "requests_per_minute": 40,
    "per_capability": {
      "ocr": 20,
      "rerank": 80
    }
  }
}
```

## File Structure

```
nvidia-nim-unified-skill/
├── SKILL.md                    # Agent instructions (main skill file)
├── README.md                   # This file
├── nvidia-nim-unified-skill.yaml  # Generic manifest
├── agents/
│   └── openai.yaml            # Codex UI metadata
├── references/
│   ├── nim-capabilities.json  # Capability definitions
│   ├── nvidia-nim-config.example.json  # Config template
│   └── openapi/              # OpenAPI specs
└── scripts/
    └── nim_router.py         # CLI router tool
```

## Troubleshooting

### "No module named 'aiohttp'"

```bash
pip install aiohttp
```

### "Rate limited" errors

Reduce request frequency or increase rate limits in config.

### Image not loading

NVIDIA CV endpoints require base64 data URLs. The router auto-converts HTTP URLs, but for local files use absolute paths.

## License

MIT
