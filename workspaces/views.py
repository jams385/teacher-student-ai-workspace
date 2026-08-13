from pypdf.errors import PyPdfError

from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import ai_client, moderation, storage
from .forms import MaterialUploadForm, StudentJoinForm, WorkspaceForm
from .models import Flag, Material, Message, StudentSession, Workspace
from .utils import extract_pdf_text, generate_unique_join_code, keyword_frequency


def teacher_signup(request):
    """Self-serve account creation for teachers. Students never get accounts —
    they join a workspace via join code (see StudentSession)."""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Welcome! Your teacher account has been created.')
            return redirect('workspace_list')
    else:
        form = UserCreationForm()
    return render(request, 'registration/signup.html', {'form': form})


@login_required
def workspace_list(request):
    workspaces = request.user.workspaces.order_by('-created_at')
    return render(request, 'workspaces/workspace_list.html', {'workspaces': workspaces})


@login_required
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


@login_required
def workspace_detail(request, pk):
    """Workspace info + course material upload. Also where a teacher will
    eventually review transcripts — dashboard, not this step."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)

    if request.method == 'POST':
        form = MaterialUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data['file']
            content = uploaded_file.read()  # read once — reused for both extraction and upload below

            extraction_failed = False
            try:
                extracted_text = extract_pdf_text(content)
            except PyPdfError:
                extracted_text = ''
                extraction_failed = True

            try:
                storage_path = storage.upload_material(
                    workspace.id,
                    uploaded_file.name,
                    content,
                    uploaded_file.content_type or 'application/pdf',
                )
            except storage.StorageError as e:
                # Extraction outcome doesn't matter — nothing was saved.
                messages.error(request, f"Couldn't upload \"{uploaded_file.name}\": {e}")
                return redirect('workspace_detail', pk=workspace.pk)

            Material.objects.create(workspace=workspace, file=storage_path, extracted_text=extracted_text)

            if extraction_failed:
                messages.warning(
                    request,
                    f'"{uploaded_file.name}" was uploaded, but its text couldn\'t be extracted '
                    '(it may be scanned or corrupted) — it won\'t be usable as AI context yet.',
                )
            else:
                messages.success(request, f'Uploaded "{uploaded_file.name}".')
            return redirect('workspace_detail', pk=workspace.pk)
    else:
        form = MaterialUploadForm()

    return render(request, 'workspaces/workspace_detail.html', {
        'workspace': workspace,
        'materials': workspace.materials.order_by('-uploaded_at'),
        'form': form,
    })


@login_required
def workspace_dashboard(request, pk):
    """Students in this workspace with message counts, a simple
    workspace-wide keyword-frequency view of what they're asking about, and
    flagged messages (possible jailbreak attempts, per moderation.py) for
    review. Per CLAUDE.md, this stays aggregate + flagged-only — not a raw
    firehose of every message."""
    workspace = get_object_or_404(Workspace, pk=pk, teacher=request.user)

    student_sessions = (
        workspace.student_sessions
        .annotate(message_count=Count('messages', filter=Q(messages__role=Message.Role.STUDENT)))
        .order_by('-joined_at')
    )

    student_message_texts = Message.objects.filter(
        workspace=workspace, role=Message.Role.STUDENT
    ).values_list('content', flat=True)

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
    })


@login_required
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


@login_required
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


def student_join(request):
    """A student enters a join code + display name — no account, no password.

    Identity for the rest of the chat is carried by Django's own session
    cookie: `StudentSession.session_id` is set to `request.session.session_key`,
    so student_chat() below can look up "who is this browser" without any
    login system. Joining always starts a fresh browser session (flush, then
    create) so re-joining — a new tab, a different workspace, coming back
    later — never collides with a stale StudentSession row.
    """
    if request.method == 'POST':
        form = StudentJoinForm(request.POST)
        if form.is_valid():
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

    return render(request, 'workspaces/chat.html', {
        'student_session': student_session,
        'workspace': student_session.workspace,
        'chat_messages': student_session.messages.all(),
    })


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
