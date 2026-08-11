import random

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
