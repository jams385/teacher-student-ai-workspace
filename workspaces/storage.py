"""storage.py — the ONLY place in this codebase that talks to Supabase Storage.

Per CLAUDE.md: uploaded files are never written to the local filesystem —
the host's filesystem is ephemeral and files would be lost on redeploy.
Everything goes to Supabase Storage instead. `Material.file` stores the
uploaded object's *path* within the bucket — never file bytes, never a
local filesystem path.
"""

import uuid

from decouple import config
from django.utils.text import get_valid_filename
from storage3.utils import StorageException
from supabase import SupabaseException, create_client

BUCKET_NAME = 'course-materials'

# Sourced via python-decouple (same reasoning as ai_client.py's API keys):
# reads .env even when it isn't exported into the real process environment.
_SUPABASE_URL = config('SUPABASE_URL', default='')
_SUPABASE_KEY = config('SUPABASE_KEY', default='')

# Lazy: create_client() raises immediately if the URL/key are missing or
# empty, rather than only failing on first use (same situation as Gemini's
# genai.Client() in ai_client.py). Constructing it at import time would
# crash the moment this module is imported, before Supabase is set up.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
    return _client


class StorageError(Exception):
    """Raised when a Supabase Storage request fails, wrapping the underlying error."""


def upload_material(workspace_id: int, filename: str, content: bytes, content_type: str = 'application/pdf') -> str:
    """Upload a course material file to Supabase Storage.

    Args:
        workspace_id: The owning Workspace's id — used to namespace the
            object path so files from different workspaces never collide.
        filename: The original filename, sanitized before use (no path
            separators or other characters that don't belong in a storage key).
        content: The raw file bytes.
        content_type: MIME type to store the object with.

    Returns:
        The object's path within the bucket — what gets stored in
        `Material.file`.

    Raises:
        StorageError: if the upload request fails.
    """
    safe_name = get_valid_filename(filename)
    path = f'workspace_{workspace_id}/{uuid.uuid4().hex}_{safe_name}'

    try:
        _get_client().storage.from_(BUCKET_NAME).upload(
            path,
            content,
            file_options={'content-type': content_type},
        )
    except (SupabaseException, StorageException) as e:
        # SupabaseException covers _get_client() failing outright (e.g. no
        # SUPABASE_URL/SUPABASE_KEY configured yet); StorageException covers
        # the upload request itself failing (bad bucket, auth, etc).
        raise StorageError(f'Supabase Storage upload failed: {e}') from e

    return path


def delete_material(path: str) -> None:
    """Delete a course material file from Supabase Storage.

    Args:
        path: The object's path within the bucket, as stored in `Material.file`
            (i.e. exactly what upload_material returned when the file was uploaded).
            Path-generic despite the name — also reused to delete Slide image
            objects (see delete's call site in views.material_delete).

    Raises:
        StorageError: if the delete request fails.
    """
    try:
        _get_client().storage.from_(BUCKET_NAME).remove([path])
    except (SupabaseException, StorageException) as e:
        raise StorageError(f'Supabase Storage delete failed: {e}') from e


def upload_slide_image(workspace_id: int, material_id: int, index: int, content: bytes) -> str:
    """Upload one rasterized slide PNG (see utils.rasterize_pdf). Returns its object path.

    Raises:
        StorageError: if the upload request fails.
    """
    path = f'workspace_{workspace_id}/material_{material_id}/slide_{index:04d}.png'
    try:
        _get_client().storage.from_(BUCKET_NAME).upload(
            path,
            content,
            file_options={'content-type': 'image/png'},
        )
    except (SupabaseException, StorageException) as e:
        raise StorageError(f'Supabase Storage upload failed: {e}') from e
    return path


def download_file(path: str) -> bytes:
    """Fetch raw bytes for any object path in the bucket (course material or
    slide image). Server-side only — never hand a signed URL to the client;
    the Live Slideshow feature proxies image bytes through Django precisely
    so the "student can't see ahead of the teacher" bound (see
    views.slide_image) is re-checked on every single request, not just once
    at URL-issue time the way a signed URL would allow.

    Raises:
        StorageError: if the download request fails.
    """
    try:
        return _get_client().storage.from_(BUCKET_NAME).download(path)
    except (SupabaseException, StorageException) as e:
        raise StorageError(f'Supabase Storage download failed: {e}') from e
