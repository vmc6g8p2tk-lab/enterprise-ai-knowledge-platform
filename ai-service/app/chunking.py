import re


def _pieces(paragraph: str, max_chars: int) -> list[str]:
    if len(paragraph) <= max_chars:
        return [paragraph]
    sentences = re.split(r"(?<=[.!?。！？])\s+", paragraph)
    result: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            words = sentence.split()
            if len(words) == 1:  # Long unspaced text: this is the unavoidable fallback.
                words = [sentence[i:i + max_chars] for i in range(0, len(sentence), max_chars)]
            for word in words:
                if len(current) + len(word) + 1 > max_chars and current:
                    result.append(current)
                    current = ""
                if len(word) > max_chars:
                    result.extend(word[i:i + max_chars] for i in range(0, len(word), max_chars))
                else:
                    current = f"{current} {word}".strip()
        elif len(current) + len(sentence) + 1 > max_chars and current:
            result.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        result.append(current)
    return result


def chunk_text(text: str, max_chars: int = 800, overlap_chars: int = 120) -> list[str]:
    if max_chars < 100 or overlap_chars < 0 or overlap_chars >= max_chars // 2:
        raise ValueError("invalid chunk configuration")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units = [piece for paragraph in paragraphs for piece in _pieces(paragraph, max_chars - overlap_chars)]
    if not units:
        return []
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > max_chars:
            chunks.append(current)
            # Overlap is taken from the previous chunk at a word boundary when possible.
            tail = current[-overlap_chars:].lstrip() if overlap_chars else ""
            space = tail.find(" ")
            if space >= 0:
                tail = tail[space + 1:]
            current = tail
        current = f"{current}\n\n{unit}".strip() if current else unit
        if len(current) > max_chars:  # A long unit plus overlap may exceed the cap.
            chunks.append(current[:max_chars].strip())
            current = current[max_chars - overlap_chars:].strip()
    if current and (not chunks or current != chunks[-1]):
        chunks.append(current)
    return chunks
