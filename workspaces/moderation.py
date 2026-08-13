"""moderation.py — keyword-based detection of jailbreak-attempt phrasing in
student messages.

Detection-only, and secondary to the real enforcement mechanism. Per
CLAUDE.md's non-negotiable architecture rule, the AI's behavior is
constrained entirely by the system instruction ai_client.py builds
server-side from the workspace's stored mode — a student can never change
that by what they type, no matter how it's phrased. This module doesn't
change what the AI does at all; it only decides whether a student's message
is worth surfacing to the teacher dashboard for human review.

Deliberately simple pattern matching for the MVP — not exhaustive, and not
meant to be. A match here is a signal, not a verdict: it doesn't mean the
attempt worked (structurally, it can't) and it isn't proof of bad intent
(curious students paraphrase "ignore your instructions" jokingly too).
"""

import re

# Each pattern is a regex fragment, matched case-insensitively against the
# raw message text. Grouped roughly by the jailbreak "move" they represent.
JAILBREAK_PATTERNS = [
    # Trying to get the model to discard its system instructions outright.
    r"ignore (all )?(the |your )?(previous|prior|above|earlier) instructions",
    r"disregard (all )?(the |your )?(previous|prior|above|earlier) instructions",
    r"forget (all )?(the |your )?(previous|prior|above|earlier) instructions",
    r"ignore (your|the) (rules|restrictions|guidelines|programming)",
    r"disregard (your|the) (rules|restrictions|guidelines|programming)",
    r"new instructions?:",
    r"override your (instructions|rules|programming)",
    r"bypass your (rules|restrictions|instructions|filters)",

    # Trying to get the model to roleplay its way out of the restriction.
    r"pretend (you'?re|you are) (not |un)restricted",
    r"pretend to be an? (unrestricted|unfiltered|jailbroken) (ai|assistant|bot)",
    r"act as if you (have|had) (no|zero) restrictions",
    r"you (have|are under) no restrictions",
    r"without any (restrictions|rules|limits|filters)",
    r"no longer (bound|restricted|limited) by",
    r"developer mode",
    r"\bdan\b.{0,20}do anything now",
    r"do anything now",

    # Trying to get the model to expose or discuss its own system prompt.
    r"reveal your (system prompt|instructions)",
    r"what (is|are) your (system prompt|instructions)",
    r"repeat your (system prompt|instructions)",
    r"system prompt",

    r"jailbreak",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in JAILBREAK_PATTERNS]


def find_jailbreak_attempts(text: str) -> list[str]:
    """Return the phrases in `text` (verbatim substrings, original casing)
    that match a known jailbreak-attempt pattern. Empty list if none match.

    Pure text matching against the student's message — this result is only
    ever used to create Flag rows for the teacher dashboard. It must never
    be fed back into ai_client.py or otherwise influence the AI's behavior.
    """
    matches = []
    for pattern in _COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            matches.append(match.group(0))
    return matches
