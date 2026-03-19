"""Tests for pipeline command - VAL-PIPELINE-001.

Tests that the pipeline command exists and has proper CLI interface.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from nim_router import (
    chunk_text,
    format_chunks_json,
    format_chunks_markdown,
    format_chunks_text,
    load_json,
)

ROOT = Path(__file__).parent.parent
CATALOG_PATH = ROOT / "references" / "nim-capabilities.json"


class TestChunkText:
    """Test the chunk_text function."""

    def test_chunk_text_empty_string(self):
        """Test chunking empty string returns empty list."""
        result = chunk_text("", 512, 64)
        assert result == []

    def test_chunk_text_single_chunk(self):
        """Test text shorter than chunk_size returns single chunk."""
        text = "Hello world this is a short text"
        result = chunk_text(text, 512, 64)
        
        assert len(result) == 1
        assert result[0]["text"] == text
        assert result[0]["start_token"] == 0
        assert result[0]["end_token"] == 6  # 7 words, indices 0-6
        assert result[0]["chunk_index"] == 0

    def test_chunk_text_multiple_chunks(self):
        """Test text longer than chunk_size creates multiple chunks."""
        # Create a text with 10 words
        words = ["word"] * 10
        text = " ".join(words)
        
        result = chunk_text(text, 4, 1)
        
        # With chunk_size=4 and overlap=1, we expect:
        # Chunk 0: words 0-3
        # Chunk 1: words 3-6 (overlap 1)
        # Chunk 2: words 6-9 (overlap 1)
        assert len(result) == 3
        
        assert result[0]["start_token"] == 0
        assert result[0]["end_token"] == 3
        assert result[0]["chunk_index"] == 0
        
        assert result[1]["start_token"] == 3
        assert result[1]["end_token"] == 6
        assert result[1]["chunk_index"] == 1
        
        assert result[2]["start_token"] == 6
        assert result[2]["end_token"] == 9
        assert result[2]["chunk_index"] == 2

    def test_chunk_text_no_overlap(self):
        """Test chunking with no overlap."""
        words = ["word"] * 10
        text = " ".join(words)
        
        result = chunk_text(text, 4, 0)
        
        # With chunk_size=4 and overlap=0, we expect:
        # Chunk 0: words 0-3
        # Chunk 1: words 4-7
        # Chunk 2: words 8-9
        assert len(result) == 3
        
        assert result[0]["start_token"] == 0
        assert result[0]["end_token"] == 3
        
        assert result[1]["start_token"] == 4
        assert result[1]["end_token"] == 7
        
        assert result[2]["start_token"] == 8
        assert result[2]["end_token"] == 9


class TestFormatChunksJson:
    """Test the format_chunks_json function."""

    def test_format_chunks_json_structure(self):
        """Test JSON output has correct structure."""
        chunks = [
            {"text": "Hello world", "start_token": 0, "end_token": 1, "chunk_index": 0},
            {"text": "foo bar", "start_token": 2, "end_token": 3, "chunk_index": 1},
        ]
        
        result = format_chunks_json(chunks, "test_source")
        
        assert "source" in result
        assert result["source"] == "test_source"
        assert "chunk_count" in result
        assert result["chunk_count"] == 2
        assert "chunks" in result
        assert len(result["chunks"]) == 2


class TestFormatChunksMarkdown:
    """Test the format_chunks_markdown function."""

    def test_format_chunks_markdown_output(self):
        """Test markdown output format."""
        chunks = [
            {"text": "Hello world", "start_token": 0, "end_token": 1, "chunk_index": 0},
        ]
        
        result = format_chunks_markdown(chunks, "test.md")
        
        assert "# Chunks from test.md" in result
        assert "Total chunks: 1" in result
        assert "## Chunk 1 (tokens 0-1)" in result
        assert "Hello world" in result


class TestFormatChunksText:
    """Test the format_chunks_text function."""

    def test_format_chunks_text_output(self):
        """Test plain text output format."""
        chunks = [
            {"text": "Hello world", "start_token": 0, "end_token": 1, "chunk_index": 0},
            {"text": "foo bar", "start_token": 2, "end_token": 3, "chunk_index": 1},
        ]
        
        result = format_chunks_text(chunks, "test.txt")
        
        assert "Hello world" in result
        assert "foo bar" in result
        assert "#" not in result  # No markdown formatting


class TestPipelineCLI:
    """Test pipeline command CLI interface."""

    def test_pipeline_help_command(self):
        """Verify: python3 scripts/nim_router.py pipeline --help works."""
        import subprocess
        result = subprocess.run(
            ["python3", str(Path(__file__).parent.parent / "scripts" / "nim_router.py"), "pipeline", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        
        assert result.returncode == 0
        assert "--input" in result.stdout
        assert "--url" in result.stdout
        assert "--chunk-size" in result.stdout
        assert "--overlap" in result.stdout
        assert "--format" in result.stdout

    def test_pipeline_chunk_size_option_exists(self):
        """Verify --chunk-size option is available."""
        import subprocess
        result = subprocess.run(
            ["python3", str(Path(__file__).parent.parent / "scripts" / "nim_router.py"), "pipeline", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        
        assert "--chunk-size" in result.stdout

    def test_pipeline_overlap_option_exists(self):
        """Verify --overlap option is available."""
        import subprocess
        result = subprocess.run(
            ["python3", str(Path(__file__).parent.parent / "scripts" / "nim_router.py"), "pipeline", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        
        assert "--overlap" in result.stdout

    def test_pipeline_format_option_exists(self):
        """Verify --format option with json|markdown|text choices."""
        import subprocess
        result = subprocess.run(
            ["python3", str(Path(__file__).parent.parent / "scripts" / "nim_router.py"), "pipeline", "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent
        )
        
        assert "--format" in result.stdout
        assert "json" in result.stdout
        assert "markdown" in result.stdout
        assert "text" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
