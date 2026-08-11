# Teacher-Student AI Workspace

## What this app is

A controlled AI environment where students interact with an AI learning assistant, and teachers define and monitor how that AI behaves. Teachers create a "workspace" (an AI chat room) for their class, set a behavior **mode**, optionally upload course materials, and students join via a code and chat. Teachers get a dashboard to review usage and transcripts.

**Core pitch:** don't prevent AI use — teach responsible use. The AI is deliberately restricted (Socratic questioning, homework guidance without direct answers) so it supports learning instead of replacing it.

---

## Tech stack

- **Backend + Frontend:** Python, Django, HTML templates + HTMX (no separate JS framework, no React/Next.js)
- **Database:** Postgres via Supabase
- **File storage:** Supabase Storage (**not** local Django storage — the host's filesystem is ephemeral and files will be lost on redeploy)
- **AI layer:** Start with Gemini (Google AI Studio) or OpenRouter free tier for development; Claude API (Anthropic Python SDK) for anything touching real student data
- **Hosting:** Render (not Vercel — Vercel's serverless Python functions have a 10s/60s execution timeout that risks killing slower LLM responses; Render runs Django as a normal persistent server)

---

## Non-negotiable architecture rule

**The mode's behavior instructions must be sent as the `system` parameter (Claude) / `system_instruction` parameter (Gemini) on every single API call, built fresh, server-side, from the workspace's stored mode — never from anything the student typed.**

This is what makes "student tries to jailbreak the AI" structurally not work, regardless of how the attempt is phrased. It is not optional and not something later keyword-filtering should be relied on for.

```python
# ai_client.py — the ONLY place that talks to the AI provider
MODE_PROMPTS = {
    "socratic": "You are in Socratic Mode for a classroom assistant. Never give direct answers. Only respond with guiding questions that help the student reach the answer themselves.",
    "homework": "You are in Homework Mode. You may check a student's work and point out errors, but never state the correct final answer directly — only nudge the student toward it."
}

def get_ai_response(mode: str, conversation_history: list, student_message: str, course_material_context: str = "") -> str:
    system_prompt = MODE_PROMPTS[mode]
    if course_material_context:
        system_prompt += f"\n\nRelevant course material:\n{course_material_context}"

    # call Claude or Gemini here — system_prompt goes in the system/system_instruction
    # field, student_message and conversation_history go in the messages/contents field.
    # Never let student_message or anything derived from it end up in system_prompt.
    ...
```

The keyword-flagging feature ("ignore previous instructions", "pretend you're not restricted", etc.) is a **separate, secondary** feature — it's for surfacing suspicious attempts to the teacher dashboard, not for enforcing behavior. Enforcement comes entirely from the architecture above.

---

## Data model (starting point)

- `Workspace` — teacher (FK to User), name, mode (choices: `socratic`, `homework`), join_code, created_at
- `Material` — workspace (FK), file (Supabase Storage path), extracted_text, uploaded_at
- `StudentSession` — workspace (FK), display_name, session_id, joined_at
- `Message` — workspace (FK), student_session (FK), role (`student`/`ai`), content, created_at
- `Flag` — message (FK), reason (e.g. `keyword_match`), matched_text, created_at, reviewed (bool)

## In-app definitions (keep terminology consistent with these)

- **Workspace** — an AI chat room the teacher creates for students
- **Socratic Mode** — the AI answers a question with a question, never the direct answer
- **Homework Mode** — the AI checks/nudges but never gives the final answer outright

---

## MVP feature checklist

- [ ] Teacher auth (Django's built-in auth) + create/manage workspaces
- [ ] Mode selection per workspace: Socratic / Homework (hardcoded prompts, no custom editor in MVP)
- [ ] Upload course texts/PDFs → extract text (`pypdf`) → store in Supabase Storage + `Material` model
- [ ] Student joins workspace via join code (lightweight session, no full account)
- [ ] Student chat interface (HTMX-driven, no full page reloads)
- [ ] Teacher dashboard: open student transcripts, message counts, commonly asked questions (simple keyword frequency for MVP — skip real clustering)
- [ ] Keyword-based flagging of jailbreak attempts, surfaced to teacher dashboard (secondary to the architectural enforcement above)

## Explicitly out of scope for MVP (don't build unless asked)

- Custom mode editor / teacher-authored prompts
- LMS integrations (Google Classroom, Canvas)
- Per-student permission overrides
- Tab-switching / focus detection
- Real topic clustering for "common questions"
- Analytics dashboards beyond raw counts

---

## Known constraints to design around

- Supabase free tier projects pause after 7 days of inactivity — fine for dev, worth a heads-up before a real pilot
- Render free tier cold-starts after 15 min idle — first request after a quiet period will be slow
- Students may be minors — no feature should log or expose more than necessary for the teacher's legitimate monitoring purpose; keep dashboard visibility to aggregate + flagged messages rather than a full raw firehose by default

---

## Style/conventions for this project

- Plain Django conventions: `models.py`, `views.py`, `urls.py` per app
- Keep the AI-calling code isolated in one module (`ai_client.py` or similar) — never scattered inline in views
- Prefer function-based views unless a class-based view clearly reduces duplication
- HTMX for interactivity — no React, no separate frontend build step