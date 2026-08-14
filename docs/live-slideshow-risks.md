# Live Slideshow — known open risks

Tracking doc for the Live Slideshow feature (Lecture Mode: teacher presents PDF slides live, students follow along in-browser with no download, no ability to see ahead of the teacher's current slide). Written as a standalone file so it's easy to find and revisit later, rather than only living in a planning doc. Not blockers to shipping — worth periodically checking whether any of these have become real problems.

## Cost at MVP/pilot scale: none new

Rasterization runs inside the existing Render web process (no new service/dyno). It's not an AI API call, so Gemini/Claude usage is unaffected. A ~20-slide deck adds roughly 4–10MB of PNGs to the existing Supabase bucket — comfortably within free-tier storage at pilot scale. `pymupdf` itself is free to use. The one indirect cost path is the large-deck timeout risk below, which could eventually force a paid Render tier — but only at real scale, not at MVP.

## Synchronous large-deck upload

Rasterization + N sequential Supabase uploads all happen inside one upload request, with no background job queue in this app. Fine for typical 10–40 slide decks; a 100+ page deck risks a platform-level request timeout on Render. Not solved here — a future job queue (e.g. Celery, Django-RQ) would be the real fix if this becomes a problem.

## Polling load

`live_status` (the HTMX-polled endpoint students hit every 4s) is cheap — a couple of ORM lookups, no image bytes in the response. Trivial even with dozens of simultaneous students. Only worth revisiting at 100+-student scale, and tunable then (longer interval) with no infra change.

## Teacher uploads a new/different PDF mid-presentation

No special handling today — `Workspace.live_material` keeps pointing at whatever it already pointed at. Intentional, not a gap: the teacher just clicks "Present" on the new material when ready.

## No "presentation ended" detection

Ending a presentation is an explicit "Stop presenting" action (matches this app's other explicit-lifecycle patterns — generate outline, delete material). If a teacher just closes the tab without clicking Stop, the presentation stays "live" indefinitely. Acceptable known limitation for MVP — no heartbeat/session-liveness infrastructure exists to detect it.

## PyMuPDF licensing (AGPL-3.0)

`pymupdf` is AGPL-3.0 (Artifex sells a commercial license for closed-source distribution). Fine for MVP/pilot/internal use. **Revisit before any commercial launch** — `pypdfium2` (Apache/BSD, pure pip wheel) is a reasonable AGPL-free alternative to evaluate if/when that matters. `pdf2image`/poppler would reintroduce a system-package dependency this design specifically avoided.

## Storage growth

One PNG object per page, in addition to the raw file already stored. Negligible at pilot scale; grows linearly in (decks × pages). No cleanup path beyond `material_delete`'s cascade (deleting a material also deletes its slide images).

## Minor race condition on rapid Next/Prev clicks

`presenter_next`/`presenter_prev` aren't wrapped in a DB lock (`select_for_update`), so two near-simultaneous requests (e.g. a teacher double-clicking) could both read/increment the same stale `live_slide_index`. Worst case is a cosmetic double-skip, self-correctable by clicking Prev. Not worth locking for at pilot scale.

## Stated, accepted limitation: "no download" is not DRM

"No download" means no built-in download button, link, or raw-file access for students — it does not, and cannot, stop a determined user from screenshotting a rendered `<img>`. This was an explicit product decision, not a gap to close.

## Scope limitation: PDF-only, not native PPTX

The live slideshow only works for materials uploaded as PDF. A teacher with only a `.pptx` must export it to PDF first to use this feature (PPTX still works fine for the AI outline/chat grounding features). Chosen deliberately to avoid adding a Dockerfile/LibreOffice dependency to a project that currently deploys via Render's plain Python buildpack with no system packages. Revisit only if native PPTX support becomes a real, repeated pain point for teachers.
