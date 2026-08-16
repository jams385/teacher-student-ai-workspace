from collections import defaultdict

from pypdf.errors import PyPdfError

from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
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
    AccountDeletionConfirmForm, MaterialUploadForm, StudentJoinForm, StudentSignupForm, TeacherSignupForm,
    WorkspaceForm,
)
from .models import Flag, Material, Message, Profile, Slide, StudentSession, Workspace
from .utils import (
    extract_pdf_text, extract_pptx_text, generate_unique_join_code, has_meaningful_content,
    keyword_frequency, rasterize_pdf,
)


def home(request):
    """Public landing page at `/`. An already-authenticated visitor has no
    use for a marketing page, so send them straight on to their own home —
    `_is_student` is defined further down this module but resolved at call
    time, same as every other forward reference here."""
    if request.user.is_authenticated:
        return redirect('student_home' if _is_student(request.user) else 'workspace_list')
    return render(request, 'workspaces/home.html')


def teacher_signup(request):
    """Self-serve account creation for teachers."""
    if request.method == 'POST':
        form = TeacherSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user, role=Profile.Role.TEACHER)
            if form.cleaned_data.get('email'):
                user.email = form.cleaned_data['email']
                user.save(update_fields=['email'])
            login(request, user)
            messages.success(request, 'Welcome! Your teacher account has been created.')
            return redirect('workspace_list')
    else:
        form = TeacherSignupForm()
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
def teacher_settings(request):
    return render(request, 'workspaces/teacher_settings.html')


@teacher_required
def teacher_change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            # Keep the session valid — PasswordChangeForm.save() rotates the
            # stored password hash, and without this Django's session-auth-hash
            # check would invalidate the session on the very next request,
            # silently logging the teacher out mid-flow.
            update_session_auth_hash(request, form.user)
            messages.success(request, 'Your password has been changed.')
            return redirect('teacher_settings')
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, 'workspaces/teacher_change_password.html', {'form': form})


@teacher_required
def teacher_delete_account(request):
    """Permanently deletes the teacher's account and everything it owns.

    Requires re-entering the current password as an extra confirmation step
    above the plain JS confirm() every other destructive action in this app
    uses (material_delete, student_remove) — this one is far harder to walk
    back, since it cascades every workspace the teacher owns.

    Deletes Supabase Storage objects (material files + slide images) first,
    same tolerate-failure pattern as material_delete, since the DB cascade
    triggered by user.delete() below never touches Storage (Material.file /
    Slide.image are plain CharField paths, not Django FileFields).
    """
    if request.method == 'POST':
        form = AccountDeletionConfirmForm(request.POST)
        if form.is_valid() and request.user.check_password(form.cleaned_data['password']):
            user = request.user
            storage_failed = False
            for workspace in user.workspaces.prefetch_related('materials__slides'):
                for material in workspace.materials.all():
                    for slide in material.slides.all():
                        try:
                            storage.delete_material(slide.image)
                        except storage.StorageError:
                            storage_failed = True
                    try:
                        storage.delete_material(material.file)
                    except storage.StorageError:
                        storage_failed = True

            user.delete()  # cascades: Profile, Workspace -> Material -> Slide,
                            # Workspace -> StudentSession -> Message -> Flag
            # Flushes/rotates the session key so the browser can never again
            # present a cookie resolving to anything related to this account.
            logout(request)
            if storage_failed:
                messages.warning(request, "Your account was deleted, but some uploaded files couldn't be removed from storage.")
            else:
                messages.success(request, 'Your account and all its workspaces have been deleted.')
            return redirect('login')
        if not form.errors.get('password'):
            form.add_error('password', 'Incorrect password.')
    else:
        form = AccountDeletionConfirmForm()
    return render(request, 'workspaces/teacher_delete_account.html', {
        'form': form,
        'workspace_count': request.user.workspaces.count(),
    })


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
    flag-review row swap.

    Two different pages can trigger this now (workspace_detail's "Generate
    outline" card, and live_presenter's "AI Summary" panel — same feature,
    same data, just surfaced in a second place per CLAUDE.md) — a hidden
    `source` field on the form says which one to redirect back to, so a
    teacher generating it mid-presentation doesn't get bounced off the live
    screen."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    redirect_target = 'live_presenter' if request.POST.get('source') == 'live' else 'workspace_detail'

    material_text = '\n\n'.join(
        material.extracted_text
        for material in workspace.materials.all()
        if material.extracted_text
    )
    if not material_text:
        messages.warning(request, "Upload course materials with extractable text before generating an outline.")
        return redirect(redirect_target, pk=workspace.pk)

    try:
        outline = ai_client.summarize_lecture_material(material_text)
    except ai_client.AIClientError as e:
        messages.error(request, f"Couldn't generate the outline: {e}")
        return redirect(redirect_target, pk=workspace.pk)

    workspace.lecture_outline = outline
    workspace.lecture_outline_generated_at = timezone.now()
    workspace.save(update_fields=['lecture_outline', 'lecture_outline_generated_at'])
    messages.success(request, 'Lecture outline generated.')
    return redirect(redirect_target, pk=workspace.pk)


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


def _advance_slide(workspace, delta):
    """Shared by the inline presenter card's Prev/Next (workspace_detail)
    and the dedicated live-presenting screen's Prev/Next (live_presenter) —
    moves live_slide_index by delta, clamped to the deck's bounds. No-ops
    if nothing is currently live (e.g. another tab already ended it)."""
    if workspace.live_material_id:
        last_index = workspace.live_material.slides.count() - 1
        workspace.live_slide_index = max(0, min(workspace.live_slide_index + delta, last_index))
        workspace.save(update_fields=['live_slide_index'])


@teacher_required
@require_POST
def presenter_next(request, pk):
    """Teacher-triggered: advance the live slide by one, clamped to the last slide."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    _advance_slide(workspace, 1)
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


@teacher_required
@require_POST
def presenter_prev(request, pk):
    """Teacher-triggered: go back one live slide, clamped to the first slide."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    _advance_slide(workspace, -1)
    return render(request, 'workspaces/partials/_presenter.html', _presenter_context(workspace))


def _live_stage_context(workspace):
    return {
        'workspace': workspace,
        'slide_count': workspace.live_material.slides.count() if workspace.live_material_id else 0,
        'slides': workspace.live_material.slides.all() if workspace.live_material_id else [],
    }


@teacher_required
def live_presenter(request, pk):
    """Dedicated full-screen view for actively presenting Lecture Mode
    content — a bigger slide stage (plus a filmstrip of the deck) next to
    an AI Summary panel (the same whole-deck outline feature as
    workspace_detail, just surfaced here too — see generate_lecture_outline).
    Only reachable while a presentation is actually live; the small inline
    presenter card on workspace_detail is still how a teacher picks a
    material and starts presenting in the first place — this screen doesn't
    duplicate that.

    Deliberately does NOT show student chat content anywhere on this
    screen — a teacher watching a live feed of what students are typing
    while presenting reads as exactly the kind of raw-firehose monitoring
    CLAUDE.md's minors data-minimization note warns against; a teacher who
    wants that already has session_transcript."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    if not workspace.live_material_id:
        messages.info(request, "You're not currently presenting.")
        return redirect('workspace_detail', pk=workspace.pk)
    return render(request, 'workspaces/workspace_presenter_live.html', _live_stage_context(workspace))


@teacher_required
@require_POST
def live_presenter_next(request, pk):
    """Prev/Next on the live-presenting screen mutate the same
    live_slide_index as the inline card's buttons (_advance_slide), just
    re-rendering the bigger _live_stage.html partial instead of
    _presenter.html — kept as separate views (rather than branching one
    view on the request) to match this codebase's existing convention of
    one explicit view per surface (e.g. student_login/teacher_login)."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    _advance_slide(workspace, 1)
    return render(request, 'workspaces/partials/_live_stage.html', _live_stage_context(workspace))


@teacher_required
@require_POST
def live_presenter_prev(request, pk):
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    _advance_slide(workspace, -1)
    return render(request, 'workspaces/partials/_live_stage.html', _live_stage_context(workspace))


@teacher_required
@require_POST
def end_lecture(request, pk):
    """Teacher-triggered, from the dedicated live-presenting screen: same
    effect as stop_presenting, but redirects back to workspace detail
    afterward instead of htmx-swapping a partial in place — there's nothing
    left for this screen to show once presenting ends. Plain POST +
    redirect, matching generate_lecture_outline's non-HTMX style."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user, mode=Workspace.Mode.LECTURE)
    workspace.live_material = None
    workspace.live_slide_index = None
    workspace.save(update_fields=['live_material', 'live_slide_index'])
    messages.success(request, 'Lecture ended.')
    return redirect('workspace_detail', pk=workspace.pk)


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

    # list(), not a bare queryset — open_flags_count below counts unreviewed
    # ones in Python off this same list rather than firing a second
    # .filter(reviewed=False).count() query for what the template already
    # has to fetch and iterate anyway.
    flags = list(
        Flag.objects.filter(message__workspace=workspace)
        .select_related('message', 'message__student_session')
        .order_by('reviewed', '-created_at')
    )

    return render(request, 'workspaces/workspace_dashboard.html', {
        'workspace': workspace,
        'student_sessions': student_sessions,
        'student_count': len(student_sessions),
        'total_messages': sum(session.message_count for session in student_sessions),
        'open_flags_count': sum(1 for flag in flags if not flag.reviewed),
        # top_n=10, not keyword_frequency's own default of 20 — the
        # dashboard's bar chart reads better short; the 20-word cap is
        # still keyword_frequency's own default for any other caller.
        'keywords': keyword_frequency(student_message_texts, top_n=10),
        'flags': flags,
    })


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
    the student would need the join code again, and none of their prior
    transcript survives. Works the same whether the session is anonymous or
    account-linked — removing it here never touches the student's account
    itself (only this workspace's membership row), so an account-linked
    student's other joined workspaces are unaffected."""
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
    """HTMX endpoint: student sends a message. Deliberately does *not* call
    the AI here — that's the slow part (a real network round-trip to the
    provider), and this used to block on it before returning anything at
    all, so a student's own message sat invisible until the AI had *also*
    already finished replying. This endpoint now only saves the student's
    message (fast — one DB write) and returns it immediately, plus a
    "thinking" placeholder bubble that lazy-loads the actual reply via
    get_ai_reply below (hx-trigger="load" on the placeholder — a standard
    htmx pattern, fires as soon as the placeholder lands in the DOM). The
    student's bubble appears instantly; the AI's shows up once it's ready.
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

    student_message = Message.objects.create(
        workspace=student_session.workspace,
        student_session=student_session,
        role=Message.Role.STUDENT,
        content=text,
    )

    # Detection-only: never influences the AI call in get_ai_reply below,
    # only surfaces the message to the teacher dashboard for review. See
    # moderation.py.
    for matched_text in moderation.find_jailbreak_attempts(text):
        Flag.objects.create(message=student_message, matched_text=matched_text[:255])

    return render(request, 'workspaces/partials/_send_response.html', {
        'message': student_message,
    })


def get_ai_reply(request, message_pk):
    """HTMX follow-up triggered by the "thinking" placeholder send_message
    just rendered (hx-trigger="load", hx-swap="outerHTML" — this response
    replaces the placeholder in place). This is where the actual AI call
    happens, exactly per the architecture rule in CLAUDE.md — mode comes
    from the workspace record, never from the request.

    message_pk scopes this to one specific student message (rather than
    e.g. "the latest one for this session") so there's no ambiguity if a
    student somehow has two of these in flight at once.
    """
    student_session = _get_student_session(request)
    if student_session is None:
        response = HttpResponse(status=204)
        response['HX-Redirect'] = reverse('student_join')
        return response

    student_message = get_object_or_404(Message, pk=message_pk, student_session=student_session)
    workspace = student_session.workspace

    # Every prior turn *except* this one, in Gemini's role naming —
    # get_ai_response takes the new message separately (see ai_client.py).
    conversation_history = [
        {
            'role': 'user' if m.role == Message.Role.STUDENT else 'model',
            'parts': [{'text': m.content}],
        }
        for m in student_session.messages.exclude(pk=student_message.pk)
    ]

    course_material_context = '\n\n'.join(
        material.extracted_text
        for material in workspace.materials.all()
        if material.extracted_text
    )

    try:
        reply_text = ai_client.get_ai_response(
            workspace.mode, conversation_history, student_message.content, course_material_context
        )
    except ai_client.AIClientError:
        return render(request, 'workspaces/partials/_chat_error.html')

    ai_message = Message.objects.create(
        workspace=workspace,
        student_session=student_session,
        role=Message.Role.AI,
        content=reply_text,
    )

    return render(request, 'workspaces/partials/_message.html', {'message': ai_message})


# --- Student accounts -------------------------------------------------
#
# Everything above this point is the original, fully anonymous join-by-code
# flow and stays exactly as-is. What follows is an additive second path: a
# student who wants their joined workspaces + chat history to persist across
# devices/logins can hold a real account — self-serve signup, no teacher
# gating. An account changes nothing about how joining a workspace works
# (still the same join-code + display-name flow, see student_join above);
# it only adds a persistent "My Workspaces" list (student_home below).


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


def student_signup(request):
    """Self-serve account creation for students — no teacher gating. Just
    creates the account and logs them in; it doesn't join any workspace on
    its own (there's no roster/invite to auto-join from anymore) — the
    student still uses the normal join-code flow (student_join) afterward,
    same as everyone else."""
    if request.user.is_authenticated:
        messages.error(request, "You're already logged in — log out first to create a new student account.")
        return redirect('student_home' if _is_student(request.user) else 'workspace_list')

    if request.method == 'POST':
        form = StudentSignupForm(request.POST)
        if form.is_valid():
            user = form.save()
            Profile.objects.create(user=user, role=Profile.Role.STUDENT)
            if form.cleaned_data.get('email'):
                user.email = form.cleaned_data['email']
                user.save(update_fields=['email'])
            login(request, user)
            messages.success(request, 'Welcome! Join a workspace with a code to get started.')
            return redirect('student_home')
    else:
        form = StudentSignupForm()
    return render(request, 'workspaces/student_signup.html', {'form': form})


@student_required
def student_home(request):
    """A logged-in student's "My Workspaces" list — every workspace they've
    joined while authenticated, most recent first."""
    student_sessions = request.user.student_sessions.select_related('workspace').order_by('-joined_at')
    return render(request, 'workspaces/student_home.html', {'student_sessions': student_sessions})


@student_required
def student_settings(request):
    """Account settings hub — change password, per-workspace "clear my data"
    (lists every joined workspace so the student can pick one), and account
    deletion."""
    student_sessions = request.user.student_sessions.select_related('workspace').order_by('workspace__name')
    return render(request, 'workspaces/student_settings.html', {'student_sessions': student_sessions})


@student_required
def student_change_password(request):
    if request.method == 'POST':
        form = PasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            # See teacher_change_password for why this call is required, not optional.
            update_session_auth_hash(request, form.user)
            messages.success(request, 'Your password has been changed.')
            return redirect('student_settings')
    else:
        form = PasswordChangeForm(user=request.user)
    return render(request, 'workspaces/student_change_password.html', {'form': form})


@student_required
@require_POST
def student_clear_data(request, pk):
    """Deletes the student's own chat history in one workspace (cascades any
    Flags on those messages), but keeps the StudentSession row itself — the
    workspace stays in "My Workspaces" with an empty transcript rather than
    requiring the student to rejoin by code. Scoped by workspace_id=pk,
    student=request.user (not the session pk) so a tampered URL can't touch
    another student's row — same scoping student_workspace_transcript uses."""
    student_session = get_object_or_404(StudentSession, workspace_id=pk, student=request.user)
    student_session.messages.all().delete()
    messages.success(request, f'Cleared your chat history in {student_session.workspace.name}.')
    return redirect('student_settings')


@student_required
def student_delete_account(request):
    """Permanently deletes the student's login. Deliberately does NOT delete
    their chat history: StudentSession.student is on_delete=SET_NULL (see
    the model's docstring) precisely so a deleted account leaves this row as
    informative as an anonymous session already is — a teacher may have a
    legitimate ongoing reason to still see it. A student who wants their
    content gone too should use "Clear account data" first (student_clear_data).

    Requires re-entering the password, same extra-friction reasoning as
    teacher_delete_account."""
    if request.method == 'POST':
        form = AccountDeletionConfirmForm(request.POST)
        if form.is_valid() and request.user.check_password(form.cleaned_data['password']):
            request.user.delete()
            logout(request)
            messages.success(request, 'Your account has been deleted.')
            return redirect('student_login')
        if not form.errors.get('password'):
            form.add_error('password', 'Incorrect password.')
    else:
        form = AccountDeletionConfirmForm()
    return render(request, 'workspaces/student_delete_account.html', {'form': form})


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
