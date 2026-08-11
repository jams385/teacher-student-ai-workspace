import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


class AlphanumericPasswordValidator:
    """Requires the password to contain at least one letter and one digit.

    Symbols are still allowed — this only rejects letters-only or
    digits-only passwords (e.g. "password" or "12345678").
    """

    def validate(self, password, user=None):
        if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
            raise ValidationError(
                _('Your password must contain at least one letter and one number.'),
                code='password_not_alphanumeric',
            )

    def get_help_text(self):
        return _('Your password must contain at least one letter and one number.')
