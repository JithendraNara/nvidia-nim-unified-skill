#!/bin/bash
# NVIDIA NIM Unified Skill - Initialization Script

set -e

cd /tmp/nvidia-nim-unified-skill

# Install dependencies
pip install aiohttp fastapi uvicorn pytest pytest-cov pytest-asyncio httpx 2>/dev/null || true

# Create cache directory
mkdir -p ~/.cache/nim-router

# Set default env vars if not set
: "${NVIDIA_API_KEY:=}"
: "${NVIDIA_NIM_OCR_URL:=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-ocr-v1}"
: "${NVIDIA_NIM_PAGE_ELEMENTS_URL:=https://ai.api.nvidia.com/v1/cv/nvidia/nemotron-page-elements-v3}"
: "${NVIDIA_NIM_TABLE_STRUCTURE_URL:=https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-table-structure-v1}"
: "${NVIDIA_NIM_GRAPHIC_ELEMENTS_URL:=https://ai.api.nvidia.com/v1/cv/nvidia/nemoretriever-graphic-elements-v1}"

echo "NVIDIA NIM Unified Skill initialized"
echo "Cache directory: ~/.cache/nim-router"
echo "To run server: python3 -m nim_router.server"
