"""Turning a raw selection into something worth listening to.

Text grabbed off a screen is messy: PDFs hyphenate across line breaks, editors
hard-wrap paragraphs, web pages carry zero-width junk. Feeding that to a
synthesiser produces audible stumbles, so it gets tidied first.
"""

from __future__ import annotations

import re
from typing import List

# Invisible characters that survive a copy but only confuse the phonemiser:
# zero-width space/joiners, bidi marks, word joiner, BOM and soft hyphen.
# Built from code points so the source file stays free of unprintable glyphs.
_INVISIBLE_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0x200E, 0x200F, 0x2060, 0xFEFF, 0x00AD)
_INVISIBLE = re.compile("[" + "".join(map(chr, _INVISIBLE_CODEPOINTS)) + "]")
# A word broken across a line by a hyphen: "synthe-\nsiser".
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
# A single newline inside a paragraph (hard wrapping) as opposed to a blank
# line, which is a real paragraph break.
_SOFT_WRAP = re.compile(r"(?<!\n)\n(?!\n)")
_BLANK_LINES = re.compile(r"\n{2,}")
# Runs of horizontal whitespace, including the non-breaking space.
_SPACES = re.compile("[ \t" + chr(0x00A0) + "]+")
# A sentence terminator plus any closing quotes or brackets that belong with
# it. Matched rather than split on, so the closers stay with their sentence.
_SENTENCE_BOUNDARY = re.compile("[.!?][\"')\\]]*(?=\\s|$)")

# Placeholder that survives whitespace collapsing while paragraph breaks are
# parked out of the way.
_PARA = chr(0)

# Substitutions that read better aloud than the raw glyph does.
_REPLACEMENTS = [
    ("—", " - "),   # em dash
    ("–", " - "),   # en dash
    ("‘", "'"),     # curly quotes
    ("’", "'"),
    ("“", '"'),
    ("”", '"'),
    ("…", "..."),   # ellipsis
    ("•", " "),     # bullet
    ("·", " "),     # middle dot
]

MAX_CHARS = 20000


def clean_text(text: str) -> str:
    """Normalise a raw selection into speakable prose."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = text.replace(_PARA, "")
    for old, new in _REPLACEMENTS:
        text = text.replace(old, new)

    # Rejoin words split across lines, then unwrap the remaining hard wraps so
    # a paragraph becomes one long line. Real paragraph breaks are parked as a
    # placeholder first so unwrapping cannot eat them.
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _BLANK_LINES.sub(_PARA, text)
    text = _SOFT_WRAP.sub(" ", text)
    text = text.replace(_PARA, "\n\n")

    text = _SPACES.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = text.strip()

    if len(text) > MAX_CHARS:
        # Cut at a sentence boundary near the limit rather than mid-word.
        head = text[:MAX_CHARS]
        cut = max(head.rfind("."), head.rfind("!"), head.rfind("?"))
        text = head[: cut + 1] if cut > MAX_CHARS // 2 else head
    return text


def split_sentences(text: str) -> List[str]:
    """Split into sentences, treating paragraph breaks as boundaries too."""
    sentences: List[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if paragraph:
            sentences.extend(_split_paragraph(paragraph))
    return sentences


def _split_paragraph(paragraph: str) -> List[str]:
    """Cut a paragraph after each sentence terminator."""
    sentences: List[str] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(paragraph):
        piece = paragraph[start:match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def split_blocks(text: str, max_chars: int = 400) -> List[str]:
    """Group sentences into blocks of roughly `max_chars`.

    Engines that render a whole utterance before returning any audio (SAPI)
    use this so playback starts quickly and stays interruptible. A single
    sentence longer than the limit is left intact rather than chopped
    mid-clause.
    """
    blocks: List[str] = []
    current = ""
    for sentence in split_sentences(text):
        if not current:
            current = sentence
        elif len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}"
        else:
            blocks.append(current)
            current = sentence
    if current:
        blocks.append(current)
    return blocks
