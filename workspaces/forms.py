from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Workspace


class WorkspaceForm(forms.ModelForm):
    class Meta:
        model = Workspace
        fields = ['name', 'mode']
        widgets = {
            'mode': forms.RadioSelect,
        }
        labels = {
            'name': 'Workspace name',
            'mode': 'Behavior mode',
        }


class StudentJoinForm(forms.Form):
    join_code = forms.CharField(max_length=16, label='Join code')
    display_name = forms.CharField(max_length=100, label='Your name')

    def clean_join_code(self):
        code = self.cleaned_data['join_code'].strip().upper()
        try:
            # Stashed on the form so the view doesn't have to look it up
            # again after validation.
            self.workspace = Workspace.objects.get(join_code=code)
        except Workspace.DoesNotExist:
            raise forms.ValidationError(
                "We couldn't find a workspace with that join code. Double-check it and try again."
            )
        return code


class StudentSignupForm(UserCreationForm):
    """Self-serve student account creation — no teacher gating. Deliberately
    just username + password (reusing UserCreationForm's fields and the same
    password validators teacher signup uses) plus one optional email. An
    account's only purpose is a persistent "My Workspaces" list (see
    views.student_home) — joining a workspace itself is still the same
    join-code + display-name flow everyone uses (StudentJoinForm), whether
    logged in or anonymous."""

    email = forms.EmailField(required=False, label='Email (optional)')

    class Meta(UserCreationForm.Meta):
        fields = ('username',)


class AccountDeletionConfirmForm(forms.Form):
    """Role-agnostic: re-entering the current password is the confirmation
    step for account deletion, a level of friction above the plain
    JS confirm() every other destructive action in this app uses (e.g.
    material_delete, student_remove) — deliberately higher, since deleting
    an account is far harder to walk back. The view checks the password
    against request.user via check_password(); this form only validates
    that something was typed."""

    password = forms.CharField(widget=forms.PasswordInput, label='Confirm your password')


class MaterialUploadForm(forms.Form):
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB — the whole file is read into memory for text extraction + upload

    PDF_CONTENT_TYPE = 'application/pdf'
    PPTX_CONTENT_TYPE = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

    file = forms.FileField(label='Course material (PDF or PowerPoint)')

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        name = uploaded_file.name.lower()
        content_type = getattr(uploaded_file, 'content_type', '') or ''
        is_pdf = name.endswith('.pdf') or content_type == self.PDF_CONTENT_TYPE
        is_pptx = name.endswith('.pptx') or content_type == self.PPTX_CONTENT_TYPE
        if not is_pdf and not is_pptx:
            raise forms.ValidationError('Please upload a PDF or PowerPoint (.pptx) file.')
        if uploaded_file.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError('That file is too large — please keep uploads under 20MB.')
        return uploaded_file
