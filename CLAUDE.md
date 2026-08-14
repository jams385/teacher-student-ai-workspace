# Teacher-Student AI Workspace

## What this app is

A controlled AI environment where students interact with an AI learning assistant, and teachers define and monitor how that AI behaves. Teachers create a "workspace" (an AI chat room) for their class, set a behavior **mode**, optionally upload course materials, and students join via a code and chat. Teachers get a dashboard to review usage and transcripts.

**Core pitch:** don't prevent AI use — teach responsible use. The AI is deliberately restricted per mode (Socratic questioning, homework guidance without direct answers, or direct lecture-content explanation grounded in uploaded slides) so it supports learning instead of replacing it.

---

## Tech stack

- **Backend + Frontend:** Python, Django, HTML templates + HTMX (no separate JS framework, no React/Next.js)
- **Database:** Postgres via Supabase
- **File storage:** Supabase Storage (**not** local Django storage — the host's filesystem is ephemeral and files will be lost on redeploy)
- **AI layer:** Currently Gemini (`gemini-flash-latest` via `google-genai`) — the whole point of `ai_client.py` isolating the provider is that swapping to Claude later (Anthropic Python SDK, e.g. once real student data is involved) only touches that one file. Model pinned to the `-latest` alias, not a dated version — Google retires specific model IDs (hit this once already: `gemini-2.5-flash` 404'd as "no longer available to new users" mid-build).
- **Hosting:** Render (not Vercel — Vercel's serverless Python functions have a 10s/60s execution timeout that risks killing slower LLM responses; Render runs Django as a normal persistent server)

---

## Non-negotiable architecture rule

**The mode's behavior instructions must be sent as the `system` parameter (Claude) / `system_instruction` parameter (Gemini) on every single API call, built fresh, server-side, from the workspace's stored mode — never from anything the student typed.**

This is what makes "student tries to jailbreak the AI" structurally not work, regardless of how the attempt is phrased. It is not optional and not something later keyword-filtering should be relied on for.

```python
# ai_client.py — the ONLY place that talks to the AI provider
MODE_PROMPTS = {
    "socratic": "You are in Socratic Mode for a classroom assistant. Never give direct answers. Only respond with guiding questions that help the student reach the answer themselves.",
    "homework": "You are in Homework Mode. You may check a student's work and point out errors, but never state the correct final answer directly — only nudge the student toward it.",
    "lecture": "You are in Lecture Mode. The class has already covered this material, so explain and clarify directly (define terms, walk through examples, answer factual questions) grounded in the uploaded lecture material — unlike Socratic/Homework, you're not withholding answers here."
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

- `Workspace` — teacher (FK to User), name, mode (choices: `socratic`, `homework`, `lecture`), join_code, created_at, lecture_outline (Lecture Mode only — a static, whole-deck outline generated on teacher demand), lecture_outline_generated_at, live_material (FK to Material, Lecture Mode Live Slideshow — which material is currently being presented, null when nothing is live; `on_delete=SET_NULL`), live_slide_index (which slide the teacher is currently on)
- `Material` — workspace (FK), file (Supabase Storage path), extracted_text, uploaded_at — PDF or PowerPoint (`.pptx`), extraction via `pypdf`/`python-pptx` respectively; deletable by the teacher (removes both the DB row and the Supabase Storage object, plus any Slide rows/images)
- `Slide` — material (FK), index (0-based page number), image (Supabase Storage path to a rasterized PNG) — PDF-only (`utils.rasterize_pdf`, via PyMuPDF); a Material with no Slide rows just isn't eligible for the Live Slideshow
- `StudentSession` — workspace (FK), display_name, session_id, joined_at
- `Message` — workspace (FK), student_session (FK), role (`student`/`ai`), content, created_at
- `Flag` — message (FK), reason (e.g. `keyword_match`), matched_text, created_at, reviewed (bool)

## In-app definitions (keep terminology consistent with these)

- **Workspace** — an AI chat room the teacher creates for students
- **Socratic Mode** — the AI answers a question with a question, never the direct answer
- **Homework Mode** — the AI checks/nudges but never gives the final answer outright
- **Lecture Mode** — teacher uploads presentation slides (PDF/PPTX) and can generate a rough whole-deck outline for students to reference; the AI explains/clarifies lecture content directly (not answer-withholding like Socratic/Homework), grounded in the uploaded slides

---

## MVP feature checklist

- [x] Teacher auth (Django's built-in auth) + create/manage workspaces
- [x] Mode selection per workspace: Socratic / Homework / Lecture (hardcoded prompts, no custom editor in MVP)
- [x] Upload course texts/PDFs/PPTX → extract text (`pypdf` / `python-pptx`) → store in Supabase Storage + `Material` model; teacher can delete a material (DB row + Storage object)
- [x] Lecture Mode: teacher-triggered whole-deck outline generation (`ai_client.summarize_lecture_material`), shown to students alongside chat — static, no live slide-by-slide sync
- [x] Lecture Mode: Live Slideshow — teacher presents a PDF's rasterized slides live (HTMX-polled), students view in-browser (no download) and may navigate backward but never ahead of the teacher's current slide, enforced server-side on every image request (see `views.slide_image`). PDF-only — see `docs/live-slideshow-risks.md`
- [x] Student joins workspace via join code (lightweight session, no full account)
- [x] Student chat interface (HTMX-driven, no full page reloads)
- [x] Teacher dashboard: open student transcripts, message counts, commonly asked questions (simple keyword frequency for MVP — skip real clustering)
- [x] Keyword-based flagging of jailbreak attempts, surfaced to teacher dashboard (secondary to the architectural enforcement above)

All MVP checklist items are done. `docs/` has draft consent/data-notice copy for teacher signup, workspace creation, and student join — not wired into the app yet, just planning content for whenever that's picked up.

Supabase is a real, live project now (not just planned) — Postgres DB and a private `course-materials` Storage bucket are both wired up and verified end to end. Local dev reads all of this from `.env` (gitignored); nothing here is hardcoded.

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
- See `docs/live-slideshow-risks.md` for open risks/tradeoffs specific to the Live Slideshow feature (cost, PyMuPDF's AGPL license, synchronous large-deck upload limits, etc.) — worth a periodic check

---

## Style/conventions for this project

- Plain Django conventions: `models.py`, `views.py`, `urls.py` per app
- Keep the AI-calling code isolated in one module (`ai_client.py` or similar) — never scattered inline in views
- Prefer function-based views unless a class-based view clearly reduces duplication
- HTMX for interactivity — no React, no separate frontend build step