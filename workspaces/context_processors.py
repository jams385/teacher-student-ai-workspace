"""Template context shared across every page, independent of which view ran.

Currently just the teacher onboarding tour, which base.html renders once for
the whole app rather than each of the four toured views passing the same
context down by hand.
"""

from . import onboarding
from .models import Profile


def onboarding_tour(request):
    """Tour state for the current page, or {} when there's nothing to show.

    Returning {} (rather than a dict of falsy values) keeps base.html's
    include gated on a single `{% if onboarding_tour %}`, and means the
    common cases — anonymous visitors, students, and teachers who already
    finished — cost one attribute check and no query beyond the `profile`
    fetch Django caches on the user anyway.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}

    # A User with no Profile row at all (e.g. one made via createsuperuser —
    # see decorators.py, which documents the same case) has no role and so no
    # tour. Fail quiet here: a context processor raising on an admin account
    # would break every page for them, not just this feature.
    try:
        profile = user.profile
    except Profile.DoesNotExist:
        return {}

    if profile.role != Profile.Role.TEACHER:
        return {}
    if profile.onboarding_status == Profile.OnboardingStatus.DONE:
        return {}

    # resolver_match is set by the time context processors run (URL
    # resolution happens first), but is None on responses rendered outside
    # normal routing — e.g. a 404/500 handler.
    match = getattr(request, 'resolver_match', None)
    if match is None:
        return {}

    steps = onboarding.steps_for_page(match.url_name)
    if not steps:
        return {}

    seen_pages = profile.onboarding_seen_pages or []
    return {
        'onboarding_tour': {
            'status': profile.onboarding_status,
            'page': match.url_name,
            'steps': steps,
            'seen_pages': seen_pages,
            # Whether finishing *this* page completes the whole tour, so the
            # last coach mark can say "Finish" and open the completion
            # dialog without a second round trip to ask the server.
            'is_final_page': onboarding.is_complete(list(seen_pages) + [match.url_name]),
        }
    }
