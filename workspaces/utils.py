import io
import random

from pypdf import PdfReader

from .models import Workspace

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
