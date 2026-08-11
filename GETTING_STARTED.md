# Getting Started with Claude Code

## 1. Set up the folder

```bash
mkdir teacher-student-ai-workspace
cd teacher-student-ai-workspace
```

Drop these three files in here before opening Claude Code:
- `CLAUDE.md` — Claude Code reads this automatically for project context every session
- `requirements.txt`
- `GETTING_STARTED.md` (this file — safe to delete once you're rolling)

## 2. Set up environment variables

Create a `.env` file (don't commit this):

```
DJANGO_SECRET_KEY=
DATABASE_URL=                  # from Supabase project settings
SUPABASE_URL=
SUPABASE_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
```

You'll fill these in as you create your Supabase project and API accounts. Empty is fine to start — Claude Code can scaffold settings.py to read from `.env` via `python-decouple` before you have real values.

## 3. Set up a virtual environment

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Open Claude Code in this folder and give it this first prompt

```
Read CLAUDE.md for full project context. Let's start the MVP build.

Step 1: Scaffold a Django project called "config" with an app called
"workspaces" that will hold the Workspace, Material, StudentSession,
Message, and Flag models described in CLAUDE.md.

Set up settings.py to:
- read all secrets from environment variables via python-decouple
- connect to Postgres using DATABASE_URL
- use whitenoise for static files
- register the "workspaces" app

Then create the models exactly as described in CLAUDE.md's data model
section, with appropriate Django field types and a __str__ method on
each. Don't build any views or templates yet — just the project
skeleton, settings, and models. Show me the migration plan before
running it.
```

This gets you a working skeleton with the right settings and models before any feature logic — a good checkpoint to review before moving further.

## 5. Suggested next prompts, in order

Once the skeleton is reviewed and migrated:

1. *"Now build the teacher auth flow and the workspace creation view — a teacher logs in, sees a list of their workspaces, and can create a new one by picking a mode and getting a join code."*
2. *"Now build `ai_client.py` exactly following the architecture rule in CLAUDE.md — the system parameter must never come from student input. Use the Anthropic Python SDK for now. Include the MODE_PROMPTS dict from CLAUDE.md."*
3. *"Now build the student join flow — enter a join code, enter a display name, land in a chat view."*
4. *"Now build the chat view itself using HTMX — student sends a message, it posts to a Django view, the view calls ai_client.get_ai_response, saves both messages to the Message model, and returns just the new chat bubbles via a partial template for HTMX to swap in."*
5. *"Now build the material upload flow — teacher uploads a PDF from the workspace page, we extract text with pypdf, store the file in Supabase Storage, and save extracted text to the Material model."*
6. *"Now build the teacher dashboard — list of students in a workspace with message counts, a transcript viewer, and a simple keyword-frequency view of commonly asked questions."*
7. *"Now add the keyword-based flagging — check incoming student messages for jailbreak-attempt phrases and log matches to the Flag model, surfaced on the dashboard."*

## Tips for working with Claude Code on this project

- **Test the mode enforcement adversarially before building anything else on top of it.** Once step 2 above is done, manually try to break Socratic Mode with different phrasings before moving to step 3. This is the part of the app that has to actually work.
- **Ask Claude Code to explain its migration before running it** the first few times, until you're comfortable reading Django migrations yourself.
- **Keep CLAUDE.md updated** as decisions change — e.g. once you actually pick Supabase vs. self-hosted Postgres, or if you swap Gemini for Claude in production, update the file so future sessions stay consistent.
- **Commit after each working step.** Small, working commits make it much easier to roll back if a later prompt takes a wrong turn.