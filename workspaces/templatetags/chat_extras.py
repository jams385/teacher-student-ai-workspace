from django import template
from django.utils.safestring import mark_safe

from .. import utils

register = template.Library()


@register.filter
def render_ai_content(text):
    """Template filter wrapping utils.render_ai_content — already
    bleach-sanitized there, so marking safe here (not before) is the one
    place that trust boundary is crossed. Only ever apply this to
    AI-authored text (chat replies, the lecture outline), never to a
    student's own typed message — see _message.html."""
    return mark_safe(utils.render_ai_content(text or ''))
