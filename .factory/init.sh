#!/bin/bash
cd /tmp/nvidia-nim-unified-skill

# Install dependencies
pip install aiohttp httpx tiktoken 2>/dev/null || true

# Create test fixtures directory
mkdir -p tests/fixtures

echo "NVIDIA NIM Pipeline Mode initialized"
echo "Run: python3 scripts/nim_router.py --help"
