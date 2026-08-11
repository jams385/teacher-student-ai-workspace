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
