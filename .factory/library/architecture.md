# Architecture

Pipeline Mode additions to nvidia-nim-unified-skill

## New Modules

```
scripts/nim_router.py       # Enhanced with pipeline command
scripts/nim_router/
  ├── embed.py             # Text embedding capability
  ├── chunker.py          # Semantic chunking with metadata
  ├── pipeline.py          # Pipeline orchestration
  └── formatters.py        # Output formatters (json-ld, markdown, text)
```

## Pipeline Flow

```
Input (file/URL)
  → OCR / Layout Detection
  → Semantic Chunking
  → Text Embedding (optional)
  → Format Output
  → Result
```

## Chunking Strategy

- Split by semantic boundaries (headers, paragraphs)
- Configurable chunk_size (tokens) and overlap
- Preserve metadata per chunk
