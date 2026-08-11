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
