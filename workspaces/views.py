from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import StudentJoinForm, WorkspaceForm
from .models import StudentSession
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


def student_chat(request):
    student_session = None
    if request.session.session_key:
        student_session = (
            StudentSession.objects
            .filter(session_id=request.session.session_key)
            .select_related('workspace')
            .first()
        )

    if student_session is None:
        messages.info(request, 'Enter your join code to start chatting.')
        return redirect('student_join')

    return render(request, 'workspaces/chat.html', {
        'student_session': student_session,
        'workspace': student_session.workspace,
    })
