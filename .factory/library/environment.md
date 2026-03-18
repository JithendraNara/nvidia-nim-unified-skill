# Environment

Environment variables and external dependencies for NVIDIA NIM Unified Skill.

## Required Env Vars

| Variable | Description | Default |
|----------|-------------|---------|
| `NVIDIA_API_KEY` | API key for NVIDIA managed endpoints | (none - required for live calls) |
| `NVIDIA_NIM_BEARER_TOKEN` | Bearer token for self-hosted overrides | (none) |
| `NVIDIA_NIM_OCR_URL` | OCR endpoint URL | `https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1` |
| `NVIDIA_NIM_PAGE_ELEMENTS_URL` | Page elements URL | `https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-page-elements-v3` |
| `NVIDIA_NIM_TABLE_STRUCTURE_URL` | Table structure URL | `https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-table-structure-v1` |
| `NVIDIA_NIM_GRAPHIC_ELEMENTS_URL` | Graphic elements URL | `https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-graphic-elements-v1` |

## Cache Location

`~/.cache/nim-router/` - Content-addressable cache for request/response pairs

## Dependencies

- Python 3.10+
- aiohttp (async HTTP)
- fastapi + uvicorn (API server)
- pytest + pytest-asyncio + httpx (testing)
