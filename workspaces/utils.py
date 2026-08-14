import io
import random
import re
from collections import Counter

from pptx import Presentation
from pypdf import PdfReader

from .models import Workspace

# Common English function words + a few chat-filler words, so the dashboard's
# "commonly asked words" view surfaces actual topics instead of "what", "is",
# "please". Not exhaustive — this is deliberately simple keyword frequency
# for the MVP, not real NLP (see CLAUDE.md).
STOPWORDS = frozenset("""
    a about after again against all am an and any are aren't as at be
    because been before being below between both but by can can't cannot
    could couldn't did didn't do does doesn't doing don't down during each
    few for from further had hadn't has hasn't have haven't having he her
    here hers herself him himself his how i if in into is isn't it its
    itself just me more most my myself no nor not now of off on once only
    or other our ours ourselves out over own same she should shouldn't so
    some such than that the their theirs them themselves then there these
    they this those through to too under until up very was wasn't we were
    weren't what when where which while who whom why will with won't would
    wouldn't you your yours yourself yourselves please thanks thank hi
    hello hey ok okay yes ya um uh ve ll re
""".split())

_WORD_RE = re.compile(r"[a-z']+")

# Excludes visually ambiguous characters (0/O, 1/I/L) so codes are easy for
# students to read off a board and type in correctly.
JOIN_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'
JOIN_CODE_LENGTH = 6


def generate_unique_join_code():
    """Return a join code guaranteed not to collide with an existing Workspace."""
    while True:
        code = ''.join(random.choices(JOIN_CODE_ALPHABET, k=JOIN_CODE_LENGTH))
        if not Workspace.objects.filter(join_code=code).exists():
            return code


def extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes, page by page, joined with blank lines.

    Raises pypdf.errors.PyPdfError (or a subclass) if the file can't be
    parsed as a PDF at all — callers should treat that as recoverable (e.g.
    still store the raw file, just without extracted text), since
    scanned/image-only or malformed PDFs are common in the wild.
    """
    reader = PdfReader(io.BytesIO(content))
    return '\n\n'.join(page.extract_text() or '' for page in reader.pages)


def extract_pptx_text(content: bytes) -> str:
    """Extract text from PPTX bytes, slide by slide, joined with blank lines.

    Pulls text from every shape with a text frame (titles, bullets, text
    boxes) on each slide — deliberately excludes speaker notes
    (slide.notes_slide), since those are a teacher's private talking points,
    not something students saw on screen or should have surfaced to them via
    the AI chat's course-material grounding.

    Callers should treat any exception here as recoverable (same as
    extract_pdf_text) — still store the raw file, just without extracted
    text. Unlike pypdf's single PyPdfError, python-pptx doesn't expose one
    reliable exception type for "this isn't a valid .pptx" (a corrupt file
    can surface as a bad zip, a missing part, or an XML parse error
    depending on how it's broken), so callers should catch broadly here.
    """
    presentation = Presentation(io.BytesIO(content))
    slide_texts = []
    for slide in presentation.slides:
        shape_texts = [
            shape.text_frame.text
            for shape in slide.shapes
            if shape.has_text_frame and shape.text_frame.text
        ]
        slide_texts.append('\n'.join(shape_texts))
    return '\n\n'.join(slide_texts)


def keyword_frequency(text_items, top_n=20):
    """Simple word-frequency count over a batch of message text.

    Per CLAUDE.md: "simple keyword frequency for MVP — skip real
    clustering". Lowercases, strips punctuation, drops stopwords and
    one/two-letter words, and counts what's left.

    Returns a list of (word, count) tuples, most common first.
    """
    counts = Counter()
    for text in text_items:
        for word in _WORD_RE.findall(text.lower()):
            word = word.strip("'")
            if len(word) > 2 and word not in STOPWORDS:
                counts[word] += 1
    return counts.most_common(top_n)
