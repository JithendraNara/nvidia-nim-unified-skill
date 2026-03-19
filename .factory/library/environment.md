# Environment

NVIDIA NIM Pipeline Mode - Environment configuration

## Required Env Vars
- `NVIDIA_API_KEY` - NVIDIA API key (required for live calls)

## Optional Env Vars
- `NVIDIA_NIM_EMBED_URL` - Text embedding endpoint override

## Dependencies
- aiohttp - async HTTP
- httpx - URL fetching
- tiktoken - token counting for chunking

## Rate Limits
- Default: 40 rpm
- Embed: configurable
