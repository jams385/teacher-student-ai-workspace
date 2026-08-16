"""Step definitions for the new-teacher guided tour.

Pure data plus two small lookups — no DB access, no request handling — so the
tour's copy lives in one reviewable place rather than scattered across four
templates, and so tests can assert on it directly.

How the tour works (the short version):

- It is **page-driven, not a linear cursor.** ``TOUR_STEPS`` maps a url_name
  to the coach marks for that page. Whichever tour page the teacher opens,
  they get that page's steps — once — and the page is then recorded in
  ``Profile.onboarding_seen_pages``. There is no "step 7 of 12" state to get
  out of sync with a back button, a bookmark, or a typed URL.
- ``TOUR_PAGE_ORDER`` exists only to tell the *last* page from the rest, so
  the final coach mark can say "Finish" and trigger the completion dialog.
  It does not constrain what order a teacher may actually visit pages in.
- Each step's ``anchor`` is a CSS selector for a ``data-tour`` attribute in
  the corresponding template. Anchoring on a dedicated attribute rather than
  on a styling class (``.stat-cards``) or an id means a later CSS refactor
  can rename the class freely without silently breaking the tour — the
  attribute exists for no reason other than being a tour anchor.
- If an anchor is missing from the DOM (a template changed, a section is
  conditionally hidden), the client skips that step rather than failing —
  see the partial's script. A tour that quietly drops one step is a much
  better failure than one that traps the teacher on a popup pointing at
  nothing.
"""

# Ordered: dict insertion order is the tour's intended path through the app,
# which is also what TOUR_PAGE_ORDER below exposes. Keys are url_names, as
# resolved from request.resolver_match.url_name.
TOUR_STEPS = {
    'workspace_list': [
        {
            'anchor': '[data-tour="workspace-list-intro"]',
            'title': 'This is your workspace list',
            'body': (
                'Every class you teach gets its own workspace — a private AI chat room with its '
                'own behaviour rules, its own join code, and its own dashboard. Nothing is shared '
                'between them.'
            ),
        },
        {
            'anchor': '[data-tour="new-workspace"]',
            'title': 'Start with one class',
            'body': (
                'Make a workspace for a single class or section — you can create as many as you '
                'need. Click "+ New workspace" when you are ready; the tour will follow you there.'
            ),
        },
    ],
    'workspace_create': [
        {
            'anchor': '[data-tour="workspace-name"]',
            'title': 'Name it after the class',
            'body': (
                'Students see this name, so "Grade 9 Biology — Period 2" reads better than '
                '"Workspace 1". You do not need to invent a join code — one is generated for you.'
            ),
        },
        {
            'anchor': '[data-tour="mode-select"]',
            'title': 'The mode is the important choice',
            'body': (
                'A mode is what the AI is allowed to do for students in this workspace. It is '
                'applied on our server to every single message, so a student cannot talk the AI '
                'out of it — no matter how the request is phrased.'
            ),
        },
        {
            'anchor': '[data-tour="mode-select"]',
            'title': 'Pick the one that fits the lesson',
            'body': (
                'Socratic replies only with guiding questions, never the answer. Homework checks '
                'work and points out errors without stating the final answer. Lecture explains '
                'directly, grounded in slides you upload. Choose one and press Continue.'
            ),
        },
    ],
    'workspace_detail': [
        {
            'anchor': '[data-tour="join-code"]',
            'title': 'Share this code with your class',
            'body': (
                'Students open the Join page, enter this code and a display name, and they are in. '
                'No student account is required — they can make one if they want their workspaces '
                'remembered, but it is never mandatory.'
            ),
        },
        {
            'anchor': '[data-tour="upload"]',
            'title': 'Give the AI your course material',
            'body': (
                'Upload PDFs or PowerPoint files. The text is extracted and used to ground the '
                'AI’s answers in what you actually taught, instead of whatever it knows in '
                'general. Lecture Mode can present the same slides live to students.'
            ),
        },
        {
            'anchor': '[data-tour="dashboard-link"]',
            'title': 'Then keep an eye on how it is used',
            'body': (
                'The dashboard is where you see how the class is actually using the AI. Open it '
                'now to finish the tour.'
            ),
        },
    ],
    'workspace_dashboard': [
        {
            'anchor': '[data-tour="stat-cards"]',
            'title': 'The quick read',
            'body': (
                'Students joined, messages sent, and flags still open. "Messages sent" ignores '
                'filler like "thanks" or "ok", so it reflects real engagement rather than every '
                'keystroke.'
            ),
        },
        {
            'anchor': '[data-tour="students-table"]',
            'title': 'Per-student detail',
            'body': (
                'Open any student’s transcript to read their full conversation, or remove a '
                'student from the workspace. This is deliberately scoped to what you need for '
                'monitoring — not a live feed of everything being typed.'
            ),
        },
        {
            'anchor': '[data-tour="keywords"]',
            'title': 'What the class is stuck on',
            'body': (
                'The words coming up most across student questions. A fast way to spot a topic the '
                'whole class is struggling with before your next session.'
            ),
        },
        {
            'anchor': '[data-tour="flags"]',
            'title': 'Flagged attempts, for your review',
            'body': (
                'Messages phrased like an attempt to talk the AI out of its mode get surfaced here. '
                'Worth knowing about — but a flag is a detection signal, not proof the AI complied. '
                'It structurally cannot, which is the whole point of the mode.'
            ),
        },
    ],
}

# The tour's intended path. Only used to identify the final page (so its last
# coach mark reads "Finish" and opens the completion dialog) and to decide
# when every page has been seen — not to force a visiting order.
TOUR_PAGE_ORDER = list(TOUR_STEPS)


def steps_for_page(url_name):
    """Coach marks for one page, or [] if it isn't part of the tour."""
    return TOUR_STEPS.get(url_name, [])


def is_complete(seen_pages):
    """True once every tour page has been worked through.

    Tolerates unknown/stale entries in `seen_pages` (e.g. a page that was
    part of the tour in an older release and has since been removed) by
    checking coverage of TOUR_PAGE_ORDER rather than comparing lengths.
    """
    return all(page in seen_pages for page in TOUR_PAGE_ORDER)
