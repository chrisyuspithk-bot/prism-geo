"""Text chunking for RAG."""


def chunk_text(text: str, max_chars: int = 700, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks on paragraph/sentence boundaries."""
    chunks: list[str] = []
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    if not paragraphs:
        return chunks

    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 <= max_chars:
            current = (current + " " + para).strip() if current else para
        else:
            if current:
                chunks.append(current)
            # If a single paragraph exceeds max_chars, split on sentences
            if len(para) > max_chars:
                sentences = _split_sentences(para)
                buf = ""
                for s in sentences:
                    if len(buf) + len(s) <= max_chars:
                        buf = (buf + " " + s).strip() if buf else s
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = s
                if buf:
                    if current:
                        chunks.append(buf)
                    else:
                        current = buf
            else:
                current = para

    if current:
        chunks.append(current)

    # Apply overlap: append suffix of previous chunk to start of next
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            if len(prev) > overlap:
                prefix = prev[-overlap:]
                # Find a clean word boundary
                space = prefix.find(" ")
                if space > 0:
                    prefix = prefix[space + 1:]
                overlapped.append(prefix + " " + chunks[i])
            else:
                overlapped.append(chunks[i])
        return overlapped

    return chunks


def _split_sentences(text: str) -> list[str]:
    """Naive sentence split on ., !, ?, followed by space."""
    import re
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]
