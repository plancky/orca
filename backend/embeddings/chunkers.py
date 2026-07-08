r"""Per-service chunking strategies (Gmail / GCal / GDrive).

Three chunkers, one per service, because an email, a calendar event, and a
Drive file have wildly different structure and size (DATA_INGESTION §2). Common
target: ~512-token windows with ~64-token overlap where windowing is needed.

Token counting is a deliberately lightweight estimate — ``len(text) // 4`` — so
the module stays dependency-free (NO tiktoken / heavy tokenizer). Each public
chunker returns ``list[str]`` (the frozen Wave-0 signature); the sync beat that
calls them assigns ``chunk_index`` / ``token_count`` when it writes the rows.
"""

import re

_CHARS_PER_TOKEN = 4
_TARGET_TOKENS = 512
_OVERLAP_TOKENS = 64

# GDrive recursive-split separator hierarchy: coarsest structural boundary first
# (headings), then paragraphs, then lines, then sentences, then words.
_GDRIVE_SEPARATORS = ("\n#", "\n\n", "\n", ". ", " ")

# Quoted reply attribution ("On <date>, <who> wrote:") — Gmail/Apple Mail style.
_QUOTE_HEADER_RE = re.compile(r"^\s*On\b.*\bwrote:\s*$", re.IGNORECASE)
# Outlook "Original Message" / forwarded-header delimiters.
_ORIGINAL_MSG_RE = re.compile(
    r"^\s*-{2,}\s*(original message|forwarded message)\s*-{2,}\s*$", re.IGNORECASE
)
_FORWARD_HEADER_RE = re.compile(r"^\s*(From|Sent|To|Cc|Subject):\s", re.IGNORECASE)


def _estimate_tokens(text: str) -> int:
    """Lightweight token estimate (~4 chars/token, no tokenizer dependency)."""
    return len(text) // _CHARS_PER_TOKEN


def _clean_email_body(body: str) -> str:
    """Strip quoted reply history and trailing signature from an email body.

    Quoted text duplicated across a thread otherwise poisons similarity with
    near-identical vectors (DATA_INGESTION §2.1), so everything from the first
    quote attribution / original-message delimiter onward is dropped, quoted
    ``>`` lines are removed, and the RFC-3676 ``-- `` signature marker cuts the
    tail.
    """
    kept: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if _QUOTE_HEADER_RE.match(stripped) or _ORIGINAL_MSG_RE.match(stripped):
            break  # quoted history begins here — drop the remainder
        if _FORWARD_HEADER_RE.match(line) and kept:
            break  # a forwarded header block after real content
        if stripped in ("--", "-- "):
            break  # signature delimiter — drop the signature block
        if stripped.startswith(">"):
            continue  # inline-quoted line
        kept.append(line)
    return "\n".join(kept).strip()


def _tail_by_tokens(text: str, overlap_tokens: int) -> str:
    """Return the trailing ~``overlap_tokens`` of ``text`` on a word boundary."""
    if overlap_tokens <= 0:
        return ""
    words = text.split()
    budget = overlap_tokens * _CHARS_PER_TOKEN
    tail: list[str] = []
    used = 0
    for word in reversed(words):
        used += len(word) + 1
        tail.append(word)
        if used >= budget:
            break
    tail.reverse()
    return " ".join(tail)


def _window_by_tokens(
    text: str,
    target_tokens: int = _TARGET_TOKENS,
    overlap_tokens: int = _OVERLAP_TOKENS,
) -> list[str]:
    """Sliding word-boundary windows of ~``target_tokens`` with overlap.

    Short text (within budget) collapses to a single chunk; longer text is cut
    into overlapping windows without ever splitting mid-word.
    """
    text = text.strip()
    if not text:
        return []
    if _estimate_tokens(text) <= target_tokens:
        return [text]

    words = text.split()
    target_chars = target_tokens * _CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * _CHARS_PER_TOKEN
    chunks: list[str] = []
    n = len(words)
    i = 0
    while i < n:
        cur_len = 0
        j = i
        while j < n:
            add = len(words[j]) + (1 if j > i else 0)
            if j > i and cur_len + add > target_chars:
                break
            cur_len += add
            j += 1
        chunks.append(" ".join(words[i:j]))
        if j >= n:
            break
        # Step back ~overlap_chars worth of words for the next window's head,
        # always making forward progress (k advances past i).
        back = 0
        k = j
        while k > i + 1 and back < overlap_chars:
            back += len(words[k - 1]) + 1
            k -= 1
        i = k if k > i else i + 1
    return chunks


def _merge_with_overlap(
    pieces: list[str],
    target_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Greedily pack structural pieces up to the budget, overlapping tails."""
    merged: list[str] = []
    cur = ""
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        candidate = f"{cur}\n{piece}".strip() if cur else piece
        if cur and _estimate_tokens(candidate) > target_tokens:
            merged.append(cur)
            tail = _tail_by_tokens(cur, overlap_tokens)
            cur = f"{tail}\n{piece}".strip() if tail else piece
        else:
            cur = candidate
    if cur:
        merged.append(cur)
    return merged


def _recursive_split(
    text: str,
    separators: tuple[str, ...],
    target_tokens: int,
    overlap_tokens: int,
) -> list[str]:
    """Recursive structural split: headings → paragraphs → lines → sentences.

    Descends the separator hierarchy only as far as needed: a piece within the
    token budget is kept whole; an oversized piece is split on the next-finer
    separator, falling back to word windows when no structural boundary helps.
    """
    text = text.strip()
    if not text:
        return []
    if _estimate_tokens(text) <= target_tokens:
        return [text]
    if not separators:
        return _window_by_tokens(text, target_tokens, overlap_tokens)

    sep, rest = separators[0], separators[1:]
    if sep not in text:
        return _recursive_split(text, rest, target_tokens, overlap_tokens)

    pieces: list[str] = []
    for piece in text.split(sep):
        piece = piece.strip()
        if not piece:
            continue
        if _estimate_tokens(piece) > target_tokens:
            pieces.extend(
                _recursive_split(piece, rest, target_tokens, overlap_tokens)
            )
        else:
            pieces.append(piece)
    return _merge_with_overlap(pieces, target_tokens, overlap_tokens)


def chunk_gmail(subject: str, body: str) -> list[str]:
    """Chunk one Gmail message: clean quotes/signature, embed subject + body.

    Embed text is ``subject + "\n" + cleaned_body``; a short message is one
    chunk, a long body is cut into 512-token / 64-overlap windows.
    """
    cleaned = _clean_email_body(body or "")
    subject = (subject or "").strip()
    embed_text = f"{subject}\n{cleaned}".strip() if subject else cleaned
    return _window_by_tokens(embed_text)


def chunk_gcal(title: str, description: str, location: str) -> list[str]:
    """Chunk one calendar event into a single ``title + description + location``
    chunk, split only when the description overflows the token window.

    Attendees are metadata-only (DATA_INGESTION §2.2) and never embedded here.
    """
    parts = [
        p.strip()
        for p in (title or "", description or "", location or "")
        if p.strip()
    ]
    embed_text = "\n".join(parts)
    return _window_by_tokens(embed_text)


def chunk_gdrive(content: str) -> list[str]:
    """Chunk a Drive file by recursive structural split (headings → paragraphs
    → sentences), 512-token target / 64-token overlap, so a query hits the
    relevant section of a large document rather than a diluted whole-file vector.
    """
    return _recursive_split(
        content or "", _GDRIVE_SEPARATORS, _TARGET_TOKENS, _OVERLAP_TOKENS
    )
