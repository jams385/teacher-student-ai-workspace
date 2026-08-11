"""ai_client.py — the ONLY place in this codebase that talks to the AI provider.

Non-negotiable architecture rule (see CLAUDE.md): the mode's behavior
instructions are built fresh, server-side, from the workspace's stored mode,
and sent as the `system_instruction` parameter on every single call. This is
what makes "student tries to jailbreak the AI" structurally not work,
regardless of how the attempt is phrased — it is not optional and not
something later keyword-filtering should be relied on for.

Never let `student_message`, `conversation_history` content, or anything
derived from them end up in the system instruction. They only ever go in the
`contents` parameter.

Using Gemini (Google AI Studio free tier) for MVP development, per CLAUDE.md's
tech stack notes. CLAUDE.md's stated production plan is to swap to the Claude
API (Anthropic SDK) for anything touching real student data — when that
happens, this is the one module that changes; get_ai_response()'s signature
and behavior contract stay the same either way.
"""

from google import genai
from google.genai import types
from decouple import config

MODEL = "gemini-2.5-flash"  # confirm current availability/pricing at ai.google.dev before production use

MODE_PROMPTS = {
    "socratic": "You are in Socratic Mode for a classroom assistant. Never give direct answers. Only respond with guiding questions that help the student reach the answer themselves.",
    "homework": "You are in Homework Mode. You may check a student's work and point out errors, but never state the correct final answer directly — only nudge the student toward it."
}

# Sourced via python-decouple (per CLAUDE.md convention) so .env is read even
# when it isn't exported into the real process environment, which is how
# local dev is set up here.
_GEMINI_API_KEY = config('GEMINI_API_KEY', default='')

# Lazy: unlike the Anthropic SDK, genai.Client() raises immediately at
# construction if no key is found anywhere (env or explicit arg) rather than
# only when a request is made. Constructing it at import time would crash
# Django startup the moment anything imports this module, before GEMINI_API_KEY
# is even set — same "don't block dev before secrets are configured" reasoning
# as the DATABASE_URL sqlite fallback in settings.py.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=_GEMINI_API_KEY) if _GEMINI_API_KEY else genai.Client()
    return _client


class AIClientError(Exception):
    """Raised when the AI provider request fails, wrapping the underlying error."""


def get_ai_response(mode: str, conversation_history: list, student_message: str, course_material_context: str = "") -> str:
    """Get the AI's reply to a student's message.

    Args:
        mode: One of the keys in MODE_PROMPTS — always the workspace's
            stored `Workspace.mode`, never anything derived from student
            input.
        conversation_history: Prior turns already in Gemini's content
            format, e.g. [{"role": "user", "parts": [{"text": "..."}]},
            {"role": "model", "parts": [{"text": "..."}]}, ...]. The caller
            is responsible for mapping Message.role ("student"/"ai") to
            Gemini's ("user"/"model") before calling this function.
        student_message: The student's new message. Only ever placed in the
            `contents` list — never merged into the system instruction.
        course_material_context: Optional extracted text from the
            workspace's uploaded course materials. Appended to the system
            instruction server-side, same as student_message is kept out of
            it.

    Returns:
        The assistant's reply text.

    Raises:
        ValueError: if `mode` isn't a known mode.
        AIClientError: if the underlying API request fails.
    """
    if mode not in MODE_PROMPTS:
        raise ValueError(f"Unknown mode: {mode!r}. Must be one of {list(MODE_PROMPTS)}.")

    system_instruction = MODE_PROMPTS[mode]
    if course_material_context:
        system_instruction += f"\n\nRelevant course material:\n{course_material_context}"

    contents = [*conversation_history, {"role": "user", "parts": [{"text": student_message}]}]

    try:
        response = _get_client().models.generate_content(
            model=MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=1024,
            ),
        )
    except genai.errors.APIError as e:
        raise AIClientError(f"AI provider request failed: {e}") from e

    if response.text:
        return response.text

    # No text back — blocked prompt (response.prompt_feedback.block_reason) or
    # a candidate that stopped for safety/other reasons with no content. Same
    # friendly fallback either way; the model never gets a raw error to parse.
    return "I'm not able to help with that request. Let's try asking your question a different way."
