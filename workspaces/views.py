from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import WorkspaceForm
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
