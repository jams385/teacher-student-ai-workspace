from django import forms

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


class MaterialUploadForm(forms.Form):
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB — the whole file is read into memory for text extraction + upload

    file = forms.FileField(label='Course material (PDF)')

    def clean_file(self):
        uploaded_file = self.cleaned_data['file']
        name = uploaded_file.name.lower()
        content_type = getattr(uploaded_file, 'content_type', '') or ''
        if not name.endswith('.pdf') and content_type != 'application/pdf':
            raise forms.ValidationError('Please upload a PDF file.')
        if uploaded_file.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError('That file is too large — please keep uploads under 20MB.')
        return uploaded_file
