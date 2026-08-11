"""ai_client.py — the ONLY place in this codebase that talks to the AI provider.

Non-negotiable architecture rule (see CLAUDE.md): the mode's behavior
instructions are built fresh, server-side, from the workspace's stored mode,
and sent as the `system` parameter on every single call. This is what makes
"student tries to jailbreak the AI" structurally not work, regardless of how
the attempt is phrased — it is not optional and not something later
keyword-filtering should be relied on for.

Never let `student_message`, `conversation_history` content, or anything
derived from them end up in the system prompt. They only ever go in the
`messages` parameter.
"""

import anthropic
from decouple import config

MODEL = "claude-opus-5"

MODE_PROMPTS = {
    "socratic": "You are in Socratic Mode for a classroom assistant. Never give direct answers. Only respond with guiding questions that help the student reach the answer themselves.",
    "homework": "You are in Homework Mode. You may check a student's work and point out errors, but never state the correct final answer directly — only nudge the student toward it."
}

# Explicitly sourced via python-decouple (per CLAUDE.md convention), rather than
# left to the SDK's own env-var auto-detection: decouple reads ANTHROPIC_API_KEY
# from .env even when it isn't exported into the real process environment, which
# is how local dev is set up here. Only pass api_key when .env actually has one —
# passing an empty string would pin the client to that empty key and skip the
# SDK's own fallback chain (e.g. a local `ant auth login` profile). Construction
# itself never fails on a missing key; it only fails when a request is made.
_ANTHROPIC_API_KEY = config('ANTHROPIC_API_KEY', default='')
_client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY) if _ANTHROPIC_API_KEY else anthropic.Anthropic()


class AIClientError(Exception):
    """Raised when the AI provider request fails, wrapping the underlying error."""


def get_ai_response(mode: str, conversation_history: list, student_message: str, course_material_context: str = "") -> str:
    """Get the AI's reply to a student's message.

    Args:
        mode: One of the keys in MODE_PROMPTS — always the workspace's
            stored `Workspace.mode`, never anything derived from student
            input.
        conversation_history: Prior turns already in Claude's message
            format, e.g. [{"role": "user", "content": "..."},
            {"role": "assistant", "content": "..."}, ...]. The caller is
            responsible for mapping Message.role ("student"/"ai") to
            Claude's ("user"/"assistant") before calling this function.
        student_message: The student's new message. Only ever placed in the
            `messages` list — never merged into the system prompt.
        course_material_context: Optional extracted text from the
            workspace's uploaded course materials. Appended to the system
            prompt server-side, same as student_message is kept out of it.

    Returns:
        The assistant's reply text.

    Raises:
        ValueError: if `mode` isn't a known mode.
        AIClientError: if the underlying API request fails.
    """
    if mode not in MODE_PROMPTS:
        raise ValueError(f"Unknown mode: {mode!r}. Must be one of {list(MODE_PROMPTS)}.")

    system_prompt = MODE_PROMPTS[mode]
    if course_material_context:
        system_prompt += f"\n\nRelevant course material:\n{course_material_context}"

    messages = [*conversation_history, {"role": "user", "content": student_message}]

    try:
        response = _client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=messages,
        )
    except anthropic.APIError as e:
        raise AIClientError(f"AI provider request failed: {e}") from e

    if response.stop_reason == "refusal":
        return "I'm not able to help with that request. Let's try asking your question a different way."

    return "".join(block.text for block in response.content if block.type == "text")
