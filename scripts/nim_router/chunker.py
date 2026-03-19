"""Semantic chunking module for RAG pipeline.

Splits text by header and paragraph boundaries while preserving metadata.
Each chunk includes: text, page_number, section_header, source_filename, token_count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A semantic chunk with text and metadata."""
    text: str
    page_number: int = 1
    section_header: str = ""
    source_filename: str = ""
    start_token: int = 0
    end_token: int = 0
    chunk_index: int = 0
    token_count: int = 0
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "text": self.text,
            "page_number": self.page_number,
            "section_header": self.section_header,
            "source_filename": self.source_filename,
            "start_token": self.start_token,
            "end_token": self.end_token,
            "chunk_index": self.chunk_index,
            "token_count": self.token_count,
        }


@dataclass
class SemanticUnit:
    """A semantic unit (paragraph or header) from text."""
    unit_type: str  # "header" or "paragraph"
    text: str
    header_level: int = 0  # 1-6 for markdown headers, 0 for paragraphs
    line_number: int = 0


def count_tokens(text: str) -> int:
    """Count tokens (words) in text."""
    if not text or not text.strip():
        return 0
    return len(text.split())


def split_into_lines(text: str) -> list[str]:
    """Split text into lines preserving empty lines as separators."""
    lines = text.split('\n')
    return lines


def identify_semantic_units(text: str) -> list[SemanticUnit]:
    """Identify semantic units (headers and paragraphs) in text.
    
    Headers are lines starting with # (markdown-style headers).
    Paragraphs are blocks of non-header text separated by blank lines.
    
    Args:
        text: The input text
        
    Returns:
        List of SemanticUnit objects
    """
    units: list[SemanticUnit] = []
    lines = split_into_lines(text)
    
    current_paragraph_lines: list[str] = []
    current_paragraph_start_line: int = 0
    line_number = 0
    
    def flush_paragraph():
        nonlocal current_paragraph_lines, current_paragraph_start_line
        if current_paragraph_lines:
            paragraph_text = ' '.join(line.strip() for line in current_paragraph_lines if line.strip())
            if paragraph_text:
                units.append(SemanticUnit(
                    unit_type="paragraph",
                    text=paragraph_text,
                    line_number=current_paragraph_start_line
                ))
            current_paragraph_lines = []
            current_paragraph_start_line = 0
    
    for line in lines:
        line_number += 1
        stripped = line.strip()
        
        # Check if this is a header
        header_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if header_match:
            # Flush current paragraph before adding header
            flush_paragraph()
            
            header_level = len(header_match.group(1))
            header_text = header_match.group(2).strip()
            
            units.append(SemanticUnit(
                unit_type="header",
                text=header_text,
                header_level=header_level,
                line_number=line_number
            ))
        elif stripped:
            # Non-empty non-header line - accumulate into current paragraph
            if not current_paragraph_lines:
                current_paragraph_start_line = line_number
            current_paragraph_lines.append(line)
        else:
            # Empty line - flush current paragraph
            flush_paragraph()
    
    # Flush any remaining paragraph
    flush_paragraph()
    
    return units


def _split_large_unit(
    unit: SemanticUnit,
    chunk_size: int,
    page_number: int,
    section_header: str,
    source_filename: str,
    chunk_index: int,
    running_token_start: int,
    overlap: int
) -> tuple[list[Chunk], int, int]:
    """Split a large semantic unit into multiple chunks.
    
    Returns:
        Tuple of (chunks created, final chunk_index, final running_token_start)
    """
    chunks: list[Chunk] = []
    words = unit.text.split()
    accumulated_words: list[str] = []
    accumulated_count = 0
    
    for word in words:
        accumulated_words.append(word)
        accumulated_count += 1
        
        if accumulated_count == chunk_size:
            # Create a chunk
            chunk_text = " ".join(accumulated_words)
            if unit.unit_type == "header":
                chunk_text = "#" * unit.header_level + " " + chunk_text
                header_for_chunk = chunk_text
            else:
                header_for_chunk = section_header
            
            chunk = Chunk(
                text=chunk_text,
                page_number=page_number,
                section_header=header_for_chunk,
                source_filename=source_filename,
                start_token=running_token_start,
                end_token=running_token_start + accumulated_count - 1,
                chunk_index=chunk_index,
                token_count=accumulated_count
            )
            chunks.append(chunk)
            chunk_index += 1
            
            # Move running token position with overlap
            running_token_start = running_token_start + accumulated_count - overlap
            if running_token_start < 0:
                running_token_start = 0
            
            # Reset accumulator for next chunk
            accumulated_words = []
            accumulated_count = 0
    
    # Handle remaining words
    if accumulated_words:
        chunk_text = " ".join(accumulated_words)
        if unit.unit_type == "header":
            chunk_text = "#" * unit.header_level + " " + chunk_text
            header_for_chunk = chunk_text
        else:
            header_for_chunk = section_header
        
        chunk = Chunk(
            text=chunk_text,
            page_number=page_number,
            section_header=header_for_chunk,
            source_filename=source_filename,
            start_token=running_token_start,
            end_token=running_token_start + accumulated_count - 1,
            chunk_index=chunk_index,
            token_count=accumulated_count
        )
        chunks.append(chunk)
        chunk_index += 1
        
        # Update running position
        running_token_start = running_token_start + accumulated_count - overlap
        if running_token_start < 0:
            running_token_start = 0
    
    return chunks, chunk_index, running_token_start


def semantic_chunk_text(
    text: str,
    chunk_size: int = 512,
    overlap: int = 64,
    source_filename: str = "",
    page_number: int = 1
) -> list[Chunk]:
    """Split text into chunks respecting semantic boundaries.
    
    Prefers splitting at header and paragraph boundaries rather than
    splitting in the middle of semantic units. Tries to keep each chunk
    close to chunk_size while not splitting mid-paragraph.
    
    Args:
        text: The text to chunk
        chunk_size: Target number of tokens per chunk (soft limit)
        overlap: Number of tokens to overlap between chunks
        source_filename: Source filename for metadata
        page_number: Page number for metadata
        
    Returns:
        List of Chunk objects with text and metadata
    """
    if not text or not text.strip():
        return []
    
    # Identify semantic units
    semantic_units = identify_semantic_units(text)
    
    if not semantic_units:
        return []
    
    chunks: list[Chunk] = []
    current_chunk_units: list[SemanticUnit] = []
    current_token_count = 0
    current_section_header = ""
    pending_section = ""  # Tracks which section the chunk's content belongs to
    chunk_start_section = ""  # Section at the START of current chunk
    chunk_index = 0
    
    # Track running token position
    running_token_start = 0
    
    def finish_chunk(units: list[SemanticUnit], start_pos: int, active_section: str) -> Chunk:
        """Create a chunk from accumulated units.
        
        Args:
            units: The semantic units to combine into a chunk
            start_pos: Starting token position
            active_section: Fallback section_header if no header in units
        """
        nonlocal chunk_index, running_token_start
        
        if not units:
            return Chunk(text="", token_count=0)
        
        # Build chunk text - preserve headers, join paragraphs with space
        # Determine section: only use header if it's the FIRST unit in the chunk;
        # otherwise use active_section (the section the content belongs to)
        chunk_section = active_section
        if units and units[0].unit_type == "header":
            chunk_section = units[0].text
        
        chunk_text_parts: list[str] = []
        for unit in units:
            if unit.unit_type == "header":
                chunk_text_parts.append(f"# {unit.text}")
            else:
                chunk_text_parts.append(unit.text)
        
        combined_text = " ".join(chunk_text_parts)
        token_count = count_tokens(combined_text)
        
        chunk = Chunk(
            text=combined_text,
            page_number=page_number,
            section_header=chunk_section,
            source_filename=source_filename,
            start_token=start_pos,
            end_token=start_pos + token_count - 1,
            chunk_index=chunk_index,
            token_count=token_count
        )
        
        chunk_index += 1
        running_token_start = start_pos + token_count - overlap
        if running_token_start < 0:
            running_token_start = 0
        
        return chunk
    
    for unit in semantic_units:
        unit_tokens = count_tokens(unit.text)
        
        # If the unit itself exceeds chunk_size, split it directly into chunks
        if unit_tokens > chunk_size:
            # First, finish any pending chunk
            if current_chunk_units:
                # Use chunk_start_section which tracks where the chunk started
                chunk = finish_chunk(current_chunk_units, running_token_start, chunk_start_section)
                chunks.append(chunk)
                current_chunk_units = []
                current_token_count = 0
                # chunk_start_section will be reset below when we start new chunk
            
            # Split this large unit into multiple chunks
            # Use pending_section if set (content section), else current_section_header
            section_for_split = pending_section if pending_section else current_section_header
            unit_chunks, chunk_index, running_token_start = _split_large_unit(
                unit, chunk_size, page_number, section_for_split,
                source_filename, chunk_index, running_token_start, overlap
            )
            chunks.extend(unit_chunks)
            pending_section = ""  # Reset after split
            chunk_start_section = ""  # Reset for next chunk
            continue
        
        # If adding this unit exceeds chunk_size, finish current chunk first
        if current_chunk_units and current_token_count + unit_tokens > chunk_size:
            # Use chunk_start_section which tracks where the chunk started
            chunk = finish_chunk(current_chunk_units, running_token_start, chunk_start_section)
            chunks.append(chunk)
            current_chunk_units = []
            current_token_count = 0
            # pending_section carries over - it represents the section content belongs to
            # Set chunk_start_section for the next chunk to be the section its content will come from
            chunk_start_section = pending_section if pending_section else current_section_header
        
        # Add current unit to chunk
        if unit.unit_type == "header":
            current_section_header = unit.text
            # If we're starting a new chunk (current_chunk_units just got reset or is empty),
            # set chunk_start_section to this header
            if not current_chunk_units:
                chunk_start_section = unit.text
        else:
            # This is content - update the section context
            pending_section = current_section_header
            # If we're starting a new chunk, the content section becomes chunk_start_section
            if not current_chunk_units:
                chunk_start_section = current_section_header
        
        current_chunk_units.append(unit)
        current_token_count += unit_tokens
    
    # Don't forget the last chunk
    if current_chunk_units:
        chunk = finish_chunk(current_chunk_units, running_token_start, chunk_start_section if chunk_start_section else (pending_section if pending_section else current_section_header))
        chunks.append(chunk)
    
    return chunks


def format_semantic_chunks_json(chunks: list[Chunk], source: str) -> dict[str, Any]:
    """Format semantic chunks as JSON with metadata.
    
    Args:
        chunks: List of Chunk objects
        source: Source identifier (file path or URL)
        
    Returns:
        Dict with chunks and metadata
    """
    return {
        "source": source,
        "chunk_count": len(chunks),
        "chunks": [chunk.to_dict() for chunk in chunks]
    }


def format_semantic_chunks_markdown(chunks: list[Chunk], source: str) -> str:
    """Format semantic chunks as readable markdown.
    
    Args:
        chunks: List of Chunk objects
        source: Source identifier (file path or URL)
        
    Returns:
        Markdown-formatted string
    """
    lines = [f"# Chunks from {source}", ""]
    lines.append(f"Total chunks: {len(chunks)}\n")
    
    for chunk in chunks:
        lines.append(f"## Chunk {chunk.chunk_index + 1}")
        
        # Add metadata
        metadata_parts = []
        if chunk.section_header:
            metadata_parts.append(f"Section: {chunk.section_header}")
        if chunk.page_number:
            metadata_parts.append(f"Page: {chunk.page_number}")
        if chunk.source_filename:
            metadata_parts.append(f"File: {chunk.source_filename}")
        metadata_parts.append(f"Tokens: {chunk.token_count}")
        
        if metadata_parts:
            lines.append(f"* {', '.join(metadata_parts)}")
        
        lines.append("")
        lines.append(chunk.text)
        lines.append("")
        lines.append("---")
        lines.append("")
    
    return "\n".join(lines)


def format_semantic_chunks_text(chunks: list[Chunk], source: str) -> str:
    """Format semantic chunks as plain text suitable for embedding pipelines.
    
    Args:
        chunks: List of Chunk objects
        source: Source identifier (file path or URL)
        
    Returns:
        Plain text string
    """
    lines = []
    for chunk in chunks:
        # Include section header as context if present
        if chunk.section_header:
            lines.append(f"[{chunk.section_header}]")
        lines.append(chunk.text)
        lines.append("")  # Empty line between chunks
    
    return "\n".join(lines).strip()


def format_semantic_chunks_jsonld(
    chunks: list[Chunk],
    source: str,
    embeddings: list[list[float]] | None = None
) -> dict[str, Any]:
    """Format semantic chunks as JSON-LD for vector DB ingestion.
    
    JSON-LD format with @context, @type, and embeddings for each chunk.
    Uses Schema.org vocabulary for structured data.
    
    Args:
        chunks: List of Chunk objects
        source: Source identifier (file path or URL)
        embeddings: Optional list of embedding vectors, one per chunk.
                   If provided, must have same length as chunks.
        
    Returns:
        JSON-LD compliant dict with chunks and metadata
    """
    item_list_elements = []
    
    for idx, chunk in enumerate(chunks):
        # Build metadata object
        metadata = {
            "pageNumber": chunk.page_number,
            "sourceFilename": chunk.source_filename,
            "startToken": chunk.start_token,
            "endToken": chunk.end_token,
            "tokenCount": chunk.token_count,
        }
        if chunk.section_header:
            metadata["sectionHeader"] = chunk.section_header
        
        # Build list item
        list_item: dict[str, Any] = {
            "@type": "ListItem",
            "position": chunk.chunk_index + 1,
            "text": chunk.text,
            "metadata": metadata,
        }
        
        # Add embedding if provided
        if embeddings and idx < len(embeddings):
            list_item["embedding"] = embeddings[idx]
        
        item_list_elements.append(list_item)
    
    # Build JSON-LD document
    jsonld: dict[str, Any] = {
        "@context": "https://schema.org/",
        "@type": "ItemList",
        "source": source,
        "chunkCount": len(chunks),
        "numberOfItems": len(chunks),
        "itemListElement": item_list_elements,
    }
    
    return jsonld
