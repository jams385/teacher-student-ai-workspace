# Frontend Build Workflow (VS Code + Claude Code extension)

Building a real frontend from the Figma design. Working doc — check off as you go.

**Starting state:** 26 templates, a 60-line `base.html`, no `static/` directory, no CSS file
at all. This is "build a design system from scratch, then apply it 26 times," not "restyle."
That framing drives everything below.

---

## VS Code layout

Three things open at once:

- **Claude panel** — `Cmd+Esc` (Mac). Docks beside the editor.
- **Integrated terminal** — split into two: one running `runserver` (leave it), one free for git.
- **Browser** — a real Chrome/Safari window on `localhost:8000`, on a second monitor or split
  screen. VS Code's Simple Browser can't do HTMX polling or file uploads reliably; don't use it
  for verification.

The extension shares MCP config with the CLI (`~/.claude.json`, project `.mcp.json`), so
anything set up in one shows up in the other. `/mcp` in the Claude panel lists servers and
their status.

Turn off auto-accept for edits. You want to see each diff.

---

## 0. Prep (once, ~15 min — you, not Claude)

- [ ] `git checkout -b frontend`
- [ ] `source venv/bin/activate && python manage.py runserver` in terminal 1, leave running
- [ ] In Figma: note which frames are finished, grab their links
- [ ] Add the Figma MCP, authenticate via `/mcp` in the Claude panel
- [ ] Smoke-test it: *"Using the Figma MCP, get the design context for [frame URL] and tell me
      the colors and font sizes you see."* Real hex values back = working. A connected server
      can still fail on a specific file if your account lacks access to it.

---

## 1. Establish the system

> Look at my Figma file [frame links]. Extract the design system only — colors, type scale,
> spacing scale, border radii, button/input/card styles — into `static/css/app.css` using CSS
> custom properties. Then restyle `templates/base.html` (nav, layout shell, Django messages)
> to use it. Don't touch any other template yet.
>
> Constraints: no build step, no Tailwind CLI, no npm. Plain CSS only. Don't modify `views.py`,
> `urls.py`, or any HTMX attributes.

Review the diff in VS Code. Look at the login page in the browser. **Iterate on the tokens
here** — this is the cheapest place to change your mind, since everything downstream inherits
from it. Commit when it feels right.

---

## 2. Write the system down

> Append a "Design system" section to `CLAUDE.md` — token names, component classes, layout
> rules, when to use what.

This is what stops drift in session 6. Commit.

---

## 3. Screens, in dependency order

One prompt per cluster. Review the diff, check the browser, commit. Then the next.

- [ ] **Auth** — `registration/login.html`, `signup.html`, `student_login.html`,
      `workspaces/student_signup.html`. Simplest; validates the system end to end.
- [ ] **Student chat** — `chat.html` + `partials/_message.html`, `_messages.html`. Highest
      traffic, hardest layout (full-height pane, scroll container, HTMX swaps).
- [ ] **Teacher workspace dashboard** — `workspace_dashboard.html` + `partials/_flag_row.html`
- [ ] **Workspace list / detail / form**
- [ ] **Student home + transcripts** — `student_home.html`,
      `student_workspace_transcript.html`, `session_transcript.html`
- [ ] **Settings** — both roles' settings, change password, delete account
- [ ] **Live Slideshow** — `partials/_presenter.html`, `_slideshow.html`. Last: most
      constrained, and it needs the rest of the system settled first.

Per-screen prompt shape:

> Restyle `[template]` and its partials using only the variables and classes already in
> `static/css/app.css`. Add new CSS only if genuinely needed, and tell me what you added.
> Every `hx-*` attribute and `{% %}` tag must survive unchanged.

**Never more than one cluster per turn.** The diff stays reviewable and breakage is
attributable. It feels slower and isn't.

---

## 4. Verify each screen before moving on

Don't batch this. Per screen:

- Click through it in a real browser
- Confirm HTMX still works (chat sends and swaps, slideshow polls, materials upload)
- Check it at mobile width
- Compare against the Figma frame

Regressions caught immediately are one-line fixes. Caught ten screens later they're
archaeology.

---

## 5. Where Figma is incomplete

Say so explicitly in the prompt rather than letting Claude guess:

> Figma covers [list]. For the other screens, extrapolate from the existing system — reuse
> established components and spacing. Don't invent new patterns or new colors. Flag anything
> you're unsure about instead of guessing.

---

## 6. Final sweep

- [ ] Grep for hardcoded hex values and one-off styles that snuck in
- [ ] Run existing tests
- [ ] `DEBUG=False` + `python manage.py collectstatic` locally. `settings.py` swaps to
      `CompressedManifestStaticFilesStorage` when `DEBUG` is off, and a missing file 404s every
      `{% static %}` tag. Catch this before Render does.
- [ ] Deploy; click the full teacher → student flow on the real URL
- [ ] Squash-merge or clean up the branch history

---

## Notes

- Chat and Live Slideshow have real layout constraints (full-height panes, scroll containers,
  polled partial swaps). If Figma doesn't cover them, say so in step 1 so the shell is built to
  accommodate them rather than retrofitted later.
- Nothing here touches `ai_client.py`, the mode-prompt architecture, or any view logic. If
  Claude proposes a change to those during frontend work, that's a red flag — push back.
