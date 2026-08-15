from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect
from django.urls import reverse

from .models import Profile


def _role_required(role, login_url_name, wrong_role_redirect):
    """Shared shape for teacher_required/student_required: not authenticated
    -> send to the right login page (preserving ?next=); authenticated but
    the other role -> friendly message + redirect to their own home, rather
    than treating them as anonymous.

    A User with no Profile row at all (e.g. one made via createsuperuser,
    before a Profile was ever attached) is denied same as a role mismatch,
    but redirected to the login page rather than wrong_role_redirect —
    otherwise a profile-less account would bounce forever between
    teacher_required's and student_required's "go to the other role's home"
    redirects, since neither view would ever accept them.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), login_url=reverse(login_url_name))
            profile = getattr(request.user, 'profile', None)
            if profile is None:
                messages.error(request, "Your account isn't set up yet — contact an admin.")
                return redirect_to_login(request.get_full_path(), login_url=reverse(login_url_name))
            if profile.role != role:
                messages.error(request, "That page isn't available for your account.")
                return redirect(wrong_role_redirect)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


teacher_required = _role_required(Profile.Role.TEACHER, 'login', 'student_home')
student_required = _role_required(Profile.Role.STUDENT, 'student_login', 'workspace_list')
