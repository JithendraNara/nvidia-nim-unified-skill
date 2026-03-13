# NVIDIA NIM Unified Skill

This repository is a portable skill bundle that routes free-form tasks to the correct NVIDIA NIM capability:

- OCR
- page elements
- table structure
- graphic elements
- reranking

The repository root is the skill folder. Clone it directly into a skills directory or point your skill loader at it.

## What It Includes

- `SKILL.md`: agent instructions and routing guidance
- `scripts/nim_router.py`: planner, request builder, and optional invoker
- `references/nim-capabilities.json`: normalized capability catalog
- `references/openapi/*.yaml`: vendored source OpenAPI specs
- `references/nvidia-nim-config.example.json`: endpoint/auth template
- `agents/openai.yaml`: Codex/OpenAI-facing metadata
- `nvidia-nim-unified-skill.yaml`: generic manifest for other runtimes

## Install

### OpenClaw

OpenClaw supports loading skills from shared and workspace skill directories, or from additional configured directories. The simplest install is cloning this repo directly into your shared skills directory:

```bash
git clone https://github.com/JithendraNara/nvidia-nim-unified-skill.git \
  ~/.openclaw/skills/nvidia-nim-unified
```

You can also clone it into a workspace-local `skills/` directory:

```bash
git clone https://github.com/JithendraNara/nvidia-nim-unified-skill.git \
  /path/to/workspace/skills/nvidia-nim-unified
```

If you prefer keeping the repo elsewhere, configure OpenClaw to load the repo path through `skills.load.extraDirs`.

Relevant docs:

- [OpenClaw skills docs](https://docs.openclaw.ai/tools/skills)
- [ClawHub docs](https://docs.openclaw.ai/tools/clawhub)

### Codex and Claude

This repo is intentionally plain AgentSkills format. Copy or symlink the repo into whatever local skills directory your agent runtime uses, or reference the folder directly if your runtime supports external skill roots.

## Configure Endpoints

Copy the example config and replace the URLs with your actual endpoints:

```bash
cp references/nvidia-nim-config.example.json references/nvidia-nim-config.json
```

Expected auth:

- `NVIDIA_API_KEY` for managed reranking
- `NVIDIA_NIM_BEARER_TOKEN` for self-hosted infer services when required

Expected endpoint env vars:

- `NVIDIA_NIM_OCR_URL`
- `NVIDIA_NIM_PAGE_ELEMENTS_URL`
- `NVIDIA_NIM_TABLE_STRUCTURE_URL`
- `NVIDIA_NIM_GRAPHIC_ELEMENTS_URL`

## Usage

Plan a task:

```bash
python3 scripts/nim_router.py plan \
  --task-query "extract text from an invoice image and preserve table structure"
```

Build a rerank request:

```bash
python3 scripts/nim_router.py build-request \
  --config references/nvidia-nim-config.example.json \
  --capability rerank \
  --query-text "Which chunk mentions H100 memory bandwidth?" \
  --passage "A100 reaches over 2 TB/s." \
  --passage "H100 offers 3 TB/s of memory bandwidth per GPU."
```

Invoke a configured endpoint:

```bash
python3 scripts/nim_router.py invoke \
  --config references/nvidia-nim-config.json \
  --capability ocr \
  --image-url "https://example.com/invoice.png"
```

## ClawHub Publishing

This repository is structured so it can be published later through ClawHub without reshaping the skill:

```bash
clawhub publish . \
  --slug nvidia-nim-unified \
  --name "NVIDIA NIM Unified" \
  --version 1.0.0 \
  --tags latest
```

## Notes

- This repo unifies multiple NVIDIA services at the routing layer, not by pretending they share one OpenAPI contract.
- The four image/document capabilities are modeled as self-hosted `/v1/infer` services.
- The rerank capability targets NVIDIA's managed API endpoint.
