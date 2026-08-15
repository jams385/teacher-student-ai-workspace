from collections import defaultdict

from pypdf.errors import PyPdfError

from django.contrib.auth import login
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import ai_client, moderation, storage
from .decorators import student_required, teacher_required
from .forms import (
    MaterialUploadForm, RosterInviteForm, StudentActivationForm, StudentJoinForm, WorkspaceForm,
)
from .models import Flag, Material, Message, Profile, RosterInvite, Slide, StudentSession, Workspace
from .utils import (
    extract_pdf_text, extract_pptx_text, generate_unique_invite_code, generate_unique_join_code,
    has_meaningful_content, keyword_frequency, rasterize_pdf,
)


def teacher_signup(request):
    """Self-serve account creation for teachers. Students don't get open
    self-signup — a teacher issues each student a one-time roster invite
    code instead (see roster_add / student_signup_activate)."""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user, role=Profile.Role.TEACHER)
            login(request, user)
            messages.success(request, 'Welcome! Your teacher account has been created.')
            return redirect('workspace_list')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})


@teacher_required
def workspace_list(request):
    workspaces = request.user.workspaces.order_by('-created_at')
    return render(request, 'workspaces/workspace_list.html', {'workspaces': workspaces})


@teacher_required
def workspace_create(request):
    if request.method == 'POST':
        form = WorkspaceForm(request.POST)
        if form.is_valid():
            workspace = form.save(commit=False)
            workspace.teacher = request.user
            workspace.join_code = generate_unique_join_code()
            workspace.save()
            messages.success(
                request,
                f'Workspace "{workspace.name}" created. Join code: {workspace.join_code}',
            )
            return redirect('workspace_list')
    else:
        form = WorkspaceForm()
    return render(request, 'workspaces/workspace_form.html', {'form': form})


@teacher_required
def workspace_detail(request, pk):
    """Workspace info + course material upload. Also where a teacher will
    eventually review transcripts — dashboard, not this step."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)

    if request.method == 'POST':
        form = MaterialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data['file']
            content = uploaded_file.read()  # read once — reused for both extraction and upload below
            is_pptx = uploaded_file.name.lower().endswith('.pptx')

            extraction_failed = False
            if is_pptx:
                try:
                    extracted_text = extract_pptx_text(content)
                except Exception:
                    # python-pptx doesn't expose one reliable exception type
                    # for "this isn't a valid .pptx" (bad zip, missing part,
                    # or an XML parse error can all show up depending on how
                    # the file is broken — see extract_pptx_text's
                    # docstring), so this branch catches broadly, unlike
                    # pypdf's single clean exception below.
                    extracted_text = ''
                    extraction_failed = True
            else:
                try:
                    extracted_text = extract_pdf_text(content)
                except PyPdfError:
                    extracted_text = ''
                    extraction_failed = True

            default_content_type = MaterialUploadForm.PPTX_CONTENT_TYPE if is_pptx else MaterialUploadForm.PDF_CONTENT_TYPE
            try:
                storage_path = storage.upload_material(
                    workspace.id,
                    uploaded_file.name,
                    content,
                    uploaded_file.content_type or default_content_type,
                )
            except storage.StorageError as e:
                # Extraction outcome doesn't matter — nothing was saved.
                messages.error(request, f"Couldn't upload \"{uploaded_file.name}\": {e}")
                return redirect('workspace_detail', pk=workspace.pk)

            material = Material.objects.create(workspace=workspace, file=storage_path, extracted_text=extracted_text)

            # Live Slideshow eligibility (PDF-only — see Slide model and
            # utils.rasterize_pdf's docstring for why PPTX isn't rasterized).
            # A rasterization failure never blocks storing the Material —
            # it just means material.slides stays empty, which is also
            # exactly the signal the "Present" button's availability uses,
            # so no separate eligibility flag is needed.
            rasterization_failed = False
            if not is_pptx:
                try:
                    page_images = rasterize_pdf(content)
                except Exception:
                    # PyMuPDF doesn't expose one clean exception type either
                    # (see rasterize_pdf's docstring) — catch broadly here
                    # too, same reasoning as the PPTX extraction branch above.
                    rasterization_failed = True
                else:
                    slides = []
                    for index, image_bytes in enumerate(page_images):
                        try:
                            image_path = storage.upload_slide_image(workspace.id, material.id, index, image_bytes)
                        except storage.StorageError:
                            rasterization_failed = True
                            break
                        slides.append(Slide(material=material, index=index, image=image_path))
                    else:
                        Slide.objects.bulk_create(slides)

            if extraction_failed and rasterization_failed:
                messages.warning(
                    request,
                    f'"{uploaded_file.name}" was uploaded, but its text and slide images couldn\'t be '
                    'extracted (it may be scanned or corrupted) — it won\'t be usable as AI context, '
                    'an outline source, or a live slideshow yet.',
                )
            elif extraction_failed:
                messages.warning(
                    request,
                    f'"{uploaded_file.name}" was uploaded, but its text couldn\'t be extracted '
                    '(it may be scanned or corrupted) — it won\'t be usable as AI context yet.',
                )
            elif rasterization_failed:
                messages.warning(
                    request,
                    f'"{uploaded_file.name}" was uploaded, but its slide images couldn\'t be generated '
                    '— it won\'t be available for the live slideshow.',
                )
            else:
                messages.success(request, f'Uploaded "{uploaded_file.name}".')
            return redirect('workspace_detail', pk=workspace.pk)
    else:
        form = MaterialUploadForm()

    context = {
        'workspace': workspace,
        'materials': workspace.materials.order_by('-uploaded_at'),
        'form': form,
    }
    if workspace.mode == Workspace.Mode.LECTURE:
        context.update(_presenter_context(workspace))
    return render(request, 'workspaces/workspace_detail.html', context)


@teacher_required
@require_POST
def generate_lecture_outline(request, pk):
    """Teacher-triggered: generate (or regenerate) a Lecture Mode workspace's
    whole-deck outline from all of its materials pooled together, the same
    pooling send_message() already does for course_material_context. Plain
    POST + redirect, matching workspace_detail.html's non-HTMX style —
    HTMX in this codebase is reserved for chat send and the dashboard's
    flag-review row swap."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)

    material_text = '\n\n'.join(
        material.extracted_text
        for material in workspace.materials.all()
        if material.extracted_text
    )
    if not material_text:
        messages.warning(request, "Upload course materials with extractable text before generating an outline.")
        return redirect('workspace_detail', pk=workspace.pk)

    try:
        outline = ai_client.summarize_lecture_material(material_text)
    except ai_client.AIClientError as e:
        messages.error(request, f"Couldn't generate the outline: {e}")
        return redirect('workspace_detail', pk=workspace.pk)

    workspace.lecture_outline = outline
    workspace.lecture_outline_generated_at = timezone.now()
    workspace.save(update_fields=['lecture_outline', 'lecture_outline_generated_at'])
    messages.success(request, 'Lecture outline generated.')
    return redirect('workspace_detail', pk=workspace.pk)


@teacher_required
@require_POST
def material_delete(request, pk, material_pk):
    """Teacher-triggered: remove an uploaded material, in any workspace mode.
    Deletes the underlying Supabase Storage objects first (the material file
    itself, plus any rasterized Slide images), then the Material row (Slide
    rows cascade automatically) — but a storage failure doesn't block the
    row deletion, since leaving a DB row the teacher explicitly asked to
    remove is worse than a rare orphaned storage object, and there's no
    retry path from here. If this material is currently being presented,
    Workspace.live_material is cleared via on_delete=SET_NULL automatically."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    material = get_object_or_404(Material, pk=material_pk, workspace=workspace)

    storage_failed = False
    for slide in material.slides.all():
        try:
            storage.delete_material(slide.image)
        except storage.StorageError:
            storage_failed = True

    try:
        storage.delete_material(material.file)
    except storage.StorageError:
        storage_failed = True

    if storage_failed:
        messages.warning(request, "Removed from the list, but couldn't delete every underlying file.")
    else:
        messages.success(request, f'Deleted "{material.file}".')

    material.delete()
    return redirect('workspace_detail', pk=workspace.pk)


def _presenter_context(workspace):
    return {
        'workspace': workspace,
        'materials_with_slides': workspace.materials.filter(slides__isnull=False).distinct().order_by('-uploaded_at'),
        'slide_count': workspace.live_material.slides.count() if workspace.live_material_id else 0,
    }


@teacher_required
@require_POST
def present_material(request, pk, material_pk):
    """Teacher-triggered: start presenting a material's slides live. Only
    materials with rasterized Slide rows (PDF-only, and only if rasterization
    succeeded — see workspace_detail's upload handler) can be presented."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    material = get_object_or_404(Material, pk=material_pk, workspace=workspace)

    if not material.slides.exists():
        messages.error(request, "This material has no slide images to present (PDF-only, and it may have failed to render).")
        return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))

    workspace.live_material = material
    workspace.live_slide_index = 0
    workspace.save(update_fields=['live_material', 'live_slide_index'])
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


@teacher_required
@require_POST
def stop_presenting(request, pk):
    """Teacher-triggered: end the live presentation. Explicit action, matching
    this app's other explicit-lifecycle patterns (generate outline, delete
    material) — there's no way to detect a teacher just closing the tab."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    workspace.live_material = None
    workspace.live_slide_index = None
    workspace.save(update_fields=['live_material', 'live_slide_index'])
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


@teacher_required
@require_POST
def presenter_next(request, pk):
    """Teacher-triggered: advance the live slide by one, clamped to the last slide."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    if workspace.live_material_id:
        last_index = workspace.live_material.slides.count() - 1
        workspace.live_slide_index = min(workspace.live_slide_index + 1, last_index)
        workspace.save(update_fields=['live_slide_index'])
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


@teacher_required
@require_POST
def presenter_prev(request, pk):
    """Teacher-triggered: go back one live slide, clamped to the first slide."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    if workspace.live_material_id:
        workspace.live_slide_index = max(workspace.live_slide_index - 1, 0)
        workspace.save(update_fields=['live_slide_index'])
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


def slide_image(request, pk, material_pk, index):
    """Proxies one rasterized slide's PNG bytes from Supabase Storage.

    This is the actual enforcement point for "students can't see ahead of
    the teacher" — it re-checks index <= workspace.live_slide_index live
    from the DB on every single request, which is exactly why slide bytes
    are proxied through this view instead of handing out a Supabase signed
    URL (a signed URL, once issued, would keep working regardless of where
    the teacher's pointer moves afterward).

    The owning teacher gets unrestricted access (any index, any material,
    even while nothing is being presented) to preview their own slides.
    Everyone else must be a joined student of this exact workspace, with
    this exact material currently live, requesting an index at or before
    the teacher's current position.
    """
    workspace = get_object_or_404(Workspace, pk=pk)
    material = get_object_or_404(Material, pk=material_pk, workspace=workspace)
    slide = get_object_or_404(Slide, material=material, index=index)

    is_owning_teacher = request.user.is_authenticated and workspace.teacher_id == request.user.id
    if not is_owning_teacher:
        student_session = _get_student_session(request)
        if (
            student_session is None
            or student_session.workspace_id != workspace.id
            or workspace.mode != Workspace.Mode.LECTURE
            or workspace.live_material_id != material.id
            or workspace.live_slide_index is None
            or index > workspace.live_slide_index
        ):
            # 403, not 404, uniformly for every entitlement failure
            # (including "no session") — don't leak resource existence to
            # an unauthenticated caller by distinguishing the cases.
            return HttpResponseForbidden()

    try:
        content = storage.download_file(slide.image)
    except storage.StorageError:
        return HttpResponse(status=502)
    return HttpResponse(content, content_type='image/png')


def live_status(request):
    """HTMX polling endpoint: reports the teacher's current live slide
    position so a joined student's page can follow along. No pk in the URL —
    identity comes from the session cookie, same convention as
    student_chat/send_message."""
    student_session = _get_student_session(request)
    if student_session is None:
        # Session cookie expired mid-poll — same handling as send_message.
        response = HttpResponse(status=204)
        response['HX-Redirect'] = reverse('student_join')
        return response

    workspace = student_session.workspace
    slide_count = workspace.live_material.slides.count() if workspace.live_material_id else 0
    return render(request, 'workspaces/partials/_slideshow.html', {
        'workspace': workspace,
        'slide_count': slide_count,
    })


@teacher_required
def workspace_dashboard(request, pk):
    """Students in this workspace with message counts, a simple
    workspace-wide keyword-frequency view of what they're asking about, and
    flagged messages (possible jailbreak attempts, per moderation.py) for
    review. Per CLAUDE.md, this stays aggregate + flagged-only — not a raw
    firehose of every message."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)

    student_sessions = list(workspace.student_sessions.order_by('-joined_at'))

    # One query for every student message in the workspace, grouped by
    # session — avoids an N+1 while still letting message_count apply
    # has_meaningful_content per message below (a DB-level Count() can't
    # do that filtering, since it's a Python-side word check, not a field
    # lookup).
    texts_by_session = defaultdict(list)
    for student_session_id, content in Message.objects.filter(
        workspace=workspace, role=Message.Role.STUDENT
    ).values_list('student_session_id', 'content'):
        texts_by_session[student_session_id].append(content)

    student_message_texts = [text for texts in texts_by_session.values() for text in texts]

    for session in student_sessions:
        # Messages sent excludes filler-only messages (e.g. just "thanks"
        # or "the") so the count reflects substantive engagement, not
        # every keystroke — same stopword rule as "Commonly asked words"
        # below, via utils.has_meaningful_content.
        session.message_count = sum(
            1 for text in texts_by_session.get(session.id, []) if has_meaningful_content(text)
        )

    flags = (
        Flag.objects.filter(message__workspace=workspace)
        .select_related('message', 'message__student_session')
        .order_by('reviewed', '-created_at')
    )

    return render(request, 'workspaces/workspace_dashboard.html', {
        'workspace': workspace,
        'student_sessions': student_sessions,
        'keywords': keyword_frequency(student_message_texts),
        'flags': flags,
        'roster_invites': workspace.roster_invites.select_related('student').order_by('-created_at'),
        'roster_form': RosterInviteForm(),
    })


@teacher_required
@require_POST
def roster_add(request, pk):
    """Teacher adds one named student to this workspace's roster, issuing a
    one-time activation code the student redeems at student_signup_activate
    to create their own account. Account creation is gated this way rather
    than open self-signup — the teacher decides who gets one."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    form = RosterInviteForm(request.POST)
    if form.is_valid():
        invite = RosterInvite.objects.create(
            workspace=workspace,
            display_name=form.cleaned_data['display_name'],
            code=generate_unique_invite_code(),
        )
        messages.success(request, f'Activation code for "{invite.display_name}": {invite.code}')
    else:
        messages.error(request, "Couldn't add that student — enter a name.")
    return redirect('workspace_dashboard', pk=workspace.pk)


@teacher_required
@require_POST
def roster_revoke(request, pk, invite_pk):
    """Teacher cancels a not-yet-claimed roster invite. Only unclaimed
    invites are revocable — once a student has activated an account with a
    code, revoking it wouldn't undo the account, so the button simply isn't
    offered for claimed rows (see workspace_dashboard.html)."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    invite = get_object_or_404(RosterInvite, pk=invite_pk, workspace=workspace, claimed_at__isnull=True)
    invite.delete()
    messages.success(request, f'Revoked the activation code for "{invite.display_name}".')
    return redirect('workspace_dashboard', pk=workspace.pk)


@teacher_required
@require_POST
def flag_mark_reviewed(request, pk, flag_pk):
    """HTMX endpoint: teacher marks a flagged message as reviewed. Scoped to
    the teacher's own workspace via the pk in the URL, same as every other
    dashboard view. Returns just the updated row (partial) for HTMX to swap."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    flag = get_object_or_404(
        Flag.objects.select_related('message', 'message__student_session'),
        pk=flag_pk,
        message__workspace=workspace,
    )
    flag.reviewed = True
    flag.save(update_fields=['reviewed'])
    return render(request, 'workspaces/partials/_flag_row.html', {'workspace': workspace, 'flag': flag})


@teacher_required
def session_transcript(request, pk, session_pk):
    """Read-only transcript for one student's session — reuses the same chat
    bubble partial the live chat view uses, just with no send form."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    student_session = get_object_or_404(StudentSession, pk=session_pk, workspace=workspace)

    return render(request, 'workspaces/session_transcript.html', {
        'workspace': workspace,
        'student_session': student_session,
        'chat_messages': student_session.messages.prefetch_related('flags'),
        # Only the teacher-facing transcript shows flag badges — students
        # never see that a message was flagged (see _message.html).
        'show_flags': True,
    })


@teacher_required
@require_POST
def student_remove(request, pk, session_pk):
    """Teacher removes a student from the workspace entirely — deletes the
    StudentSession row, which cascades to delete their Messages and any
    Flags on those messages (both FKs are on_delete=CASCADE). A full,
    permanent removal, not just cutting off further access: to come back,
    the student would need a fresh join code or roster invite, and none of
    their prior transcript survives. Works the same whether the session is
    anonymous or account-linked — removing it here never touches the
    student's account itself (only this workspace's membership row), so an
    account-linked student's other joined workspaces are unaffected."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)
    student_session = get_object_or_404(StudentSession, pk=session_pk, workspace=workspace)
    display_name = student_session.display_name
    student_session.delete()
    messages.success(request, f'Removed {display_name} from the workspace.')
    return redirect('workspace_dashboard', pk=workspace.pk)


def _is_student(user):
    profile = getattr(user, 'profile', None)
    return profile is not None and profile.role == Profile.Role.STUDENT


def _attach_active_session(request, target_session):
    """Make `target_session` the row _get_student_session() resolves to.

    Steals the browser's session_id away from whatever OTHER StudentSession
    row currently holds it — session_id is unique across the whole table,
    and only one row can be "active" (i.e. what the chat views resolve to)
    in a given browser tab at a time. This is how a logged-in student with
    several joined workspaces switches which one is "live" in this tab,
    without touching Django's own session/login state at all.

    Known limitation, not fixed here: two tabs open on two different
    workspaces under the same login will fight over session_id — the
    second tab's attach silently steals it from the first, so a stale first
    tab's next message gets filed into the second workspace's transcript
    instead of erroring out. The anonymous flow fails loudly here (redirect
    to /join/); this fails quietly. The app has never supported concurrent
    multi-tab chatting, so not solving this now.
    """
    if not request.session.session_key:
        request.session.create()
    key = request.session.session_key
    with transaction.atomic():
        StudentSession.objects.filter(session_id=key).exclude(pk=target_session.pk).update(session_id=None)
        target_session.session_id = key
        target_session.save(update_fields=['session_id'])


def _join_workspace_as_student(request, workspace, display_name):
    """Find-or-create the logged-in student's StudentSession for `workspace`
    (see the unique_student_workspace_when_authenticated constraint — a
    student gets exactly one row per workspace, so rejoining accumulates
    history in place instead of fragmenting across rows) and make it the
    active one in this browser tab."""
    student_session, _ = StudentSession.objects.update_or_create(
        student=request.user, workspace=workspace,
        defaults={'display_name': display_name},
    )
    _attach_active_session(request, student_session)
    return student_session


def student_join(request):
    """A student enters a join code + display name.

    Anonymous (the default): no account, no password. Identity for the rest
    of the chat is carried by Django's own session cookie —
    `StudentSession.session_id` is set to `request.session.session_key`, so
    student_chat() below can look up "who is this browser" without any login
    system. Joining always starts a fresh browser session (flush, then
    create) so re-joining — a new tab, a different workspace, coming back
    later — never collides with a stale StudentSession row.

    Logged-in student: same join code + display name form, but the join
    attaches to their account instead (_join_workspace_as_student) — no
    session flush, since that would also log them out (Django's auth state
    lives in the session). Their login stays put; only session_id moves.
    """
    if request.method == 'POST':
        form = StudentJoinForm(request.POST)
        if form.is_valid():
            if request.user.is_authenticated and _is_student(request.user):
                _join_workspace_as_student(request, form.workspace, form.cleaned_data['display_name'])
            else:
                request.session.flush()
                request.session.create()
                StudentSession.objects.create(
                    workspace=form.workspace,
                    display_name=form.cleaned_data['display_name'],
                    session_id=request.session.session_key,
                )
            return redirect('student_chat')
    else:
        form = StudentJoinForm()
    return render(request, 'workspaces/student_join.html', {'form': form})


def _get_student_session(request):
    """Look up "who is this browser" from the session cookie (see student_join).

    Returns None if there's no session cookie or it doesn't match a
    StudentSession — e.g. the cookie expired, or this is a fresh browser
    that never joined.
    """
    if not request.session.session_key:
        return None
    return (
        StudentSession.objects
        .filter(session_id=request.session.session_key)
        .select_related('workspace')
        .first()
    )


def student_chat(request):
    student_session = _get_student_session(request)
    if student_session is None:
        messages.info(request, 'Enter your join code to start chatting.')
        return redirect('student_join')

    workspace = student_session.workspace
    context = {
        'student_session': student_session,
        'workspace': workspace,
        'chat_messages': student_session.messages.all(),
    }
    if workspace.mode == Workspace.Mode.LECTURE:
        # Seed initial slideshow state on page load, so students don't have
        # to wait for the first live_status poll to see it (see chat.html).
        context['slide_count'] = workspace.live_material.slides.count() if workspace.live_material_id else 0
    return render(request, 'workspaces/chat.html', context)


@require_POST
def send_message(request):
    """HTMX endpoint: student sends a message, gets the AI's reply appended.

    Calls ai_client.get_ai_response exactly per the architecture rule in
    CLAUDE.md — mode comes from the workspace record, never from the
    request. Returns just the new chat bubbles (a partial), not a full page,
    for HTMX to swap into #message-list.
    """
    student_session = _get_student_session(request)
    if student_session is None:
        # Session cookie expired mid-chat. HX-Redirect tells htmx to do a
        # full page navigation instead of trying to swap partial content in.
        response = HttpResponse(status=204)
        response['HX-Redirect'] = reverse('student_join')
        return response

    text = request.POST.get('message', '').strip()
    if not text:
        return HttpResponse('')  # nothing to send — no-op, no swap content

    workspace = student_session.workspace

    # Prior turns, in Gemini's role naming — built *before* saving the new
    # student message below, since get_ai_response takes the new message
    # separately (see ai_client.py).
    conversation_history = [
        {
            'role': 'user' if m.role == Message.Role.STUDENT else 'model',
            'parts': [{'text': m.content}],
        }
        for m in student_session.messages.all()
    ]

    course_material_context = '\n\n'.join(
        material.extracted_text
        for material in workspace.materials.all()
        if material.extracted_text
    )

    student_message = Message.objects.create(
        workspace=workspace,
        student_session=student_session,
        role=Message.Role.STUDENT,
        content=text,
    )

    # Detection-only: never influences the AI call below, only surfaces the
    # message to the teacher dashboard for review. See moderation.py.
    for matched_text in moderation.find_jailbreak_attempts(text):
        Flag.objects.create(message=student_message, matched_text=matched_text[:255])

    try:
        reply_text = ai_client.get_ai_response(
            workspace.mode, conversation_history, text, course_material_context
        )
    except ai_client.AIClientError:
        return render(request, 'workspaces/partials/_messages.html', {
            'chat_messages': [student_message],
            'error': True,
        })

    ai_message = Message.objects.create(
        workspace=workspace,
        student_session=student_session,
        role=Message.Role.AI,
        content=reply_text,
    )

    return render(request, 'workspaces/partials/_messages.html', {
        'chat_messages': [student_message, ai_message],
    })


# --- Student accounts -------------------------------------------------
#
# Everything above this point is the original, fully anonymous join-by-code
# flow and stays exactly as-is. What follows is an additive second path: a
# student who wants their joined workspaces + chat history to persist across
# devices/logins can hold a real account. Account creation is gated by a
# teacher-issued RosterInvite (see roster_add above), not open self-signup.


def student_login(request):
    """Separate from the teacher's /accounts/login/ — a shared login page
    would need its own role check anyway, and this keeps the two flows
    (and their very different redirect targets) independent.

    AuthenticationForm.is_valid() only authenticates; it doesn't establish
    a session. So if the role check below fails, there's nothing to undo —
    request.user is still AnonymousUser for the rest of this request, no
    logout() call needed.
    """
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if _is_student(user):
                login(request, user)
                return redirect('student_home')
            form.add_error(None, 'This login is for students. Teachers should use the teacher log in page.')
    else:
        form = AuthenticationForm(request)
    return render(request, 'registration/student_login.html', {'form': form})


def student_signup_activate(request):
    """A student redeems a teacher-issued RosterInvite code to create their
    own account, then is auto-joined to the invite's workspace."""
    if request.user.is_authenticated:
        messages.error(request, "You're already logged in — log out first to activate a new student account.")
        return redirect('student_home' if _is_student(request.user) else 'workspace_list')

    if request.method == 'POST':
        form = StudentActivationForm(request.POST)
        if form.is_valid():
            invite = form.invite
            user = form.save()
            Profile.objects.create(user=user, role=Profile.Role.STUDENT)
            if form.cleaned_data.get('email'):
                user.email = form.cleaned_data['email']
                user.save(update_fields=['email'])
            invite.claimed_at = timezone.now()
            invite.student = user
            invite.save(update_fields=['claimed_at', 'student'])

            login(request, user)
            _join_workspace_as_student(request, invite.workspace, invite.display_name)
            messages.success(request, f'Welcome, {invite.display_name}! Your account is ready.')
            return redirect('student_home')
    else:
        form = StudentActivationForm()
    return render(request, 'workspaces/roster_activate.html', {'form': form})


@student_required
def student_home(request):
    """A logged-in student's "My Workspaces" list — every workspace they've
    joined while authenticated, most recent first."""
    student_sessions = request.user.student_sessions.select_related('workspace').order_by('-joined_at')
    return render(request, 'workspaces/student_home.html', {'student_sessions': student_sessions})


@student_required
def student_workspace_transcript(request, pk):
    """Read-only transcript for one of the logged-in student's own joined
    workspaces, plus a "Continue chatting" action. Scoped by student=
    request.user, not just the StudentSession pk — otherwise a tampered URL
    could read/attach another student's row."""
    student_session = get_object_or_404(
        StudentSession.objects.select_related('workspace'),
        workspace_id=pk, student=request.user,
    )
    return render(request, 'workspaces/student_workspace_transcript.html', {
        'workspace': student_session.workspace,
        'student_session': student_session,
        'chat_messages': student_session.messages.all(),
        # Students never see flag badges on their own messages — same rule
        # as the live chat view (see _message.html).
        'show_flags': False,
    })


@student_required
@require_POST
def student_workspace_continue(request, pk):
    """Make one of the student's already-joined workspaces the active one in
    this browser tab, then drop them into the normal live chat view — no
    join-code/display-name re-entry needed, we already have both on file."""
    student_session = get_object_or_404(
        StudentSession.objects.select_related('workspace'),
        workspace_id=pk, student=request.user,
    )
    _attach_active_session(request, student_session)
    return redirect('student_chat')
