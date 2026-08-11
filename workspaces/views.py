from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import ai_client
from .forms import StudentJoinForm, WorkspaceForm
from .models import Message, StudentSession
from .utils import generate_unique_join_code


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
