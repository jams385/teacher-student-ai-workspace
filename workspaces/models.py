from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """An AI chat room a teacher creates for a class."""

    class Mode(models.TextChoices):
        SOCRATIC = 'socratic', 'Socratic Mode'
        HOMEWORK = 'homework', 'Homework Mode'

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspaces',
    )
    name = models.CharField(max_length=255)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    join_code = models.CharField(max_length=16, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name} ({self.get_mode_display()})'


class Material(models.Model):
    """A course text/PDF uploaded to a workspace, stored in Supabase Storage."""

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='materials',
    )
    # Path/key of the file in Supabase Storage — not a Django FileField, since
    # storage is Supabase Storage, not the (ephemeral) local filesystem.
    file = models.CharField(max_length=500)
    extracted_text = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Material for {self.workspace.name}: {self.file}'


class StudentSession(models.Model):
    """A lightweight, account-free session for a student in a workspace."""

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='student_sessions',
    )
    display_name = models.CharField(max_length=100)
    session_id = models.CharField(max_length=64, unique=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.display_name} in {self.workspace.name}'


class Message(models.Model):
    """A single chat message, from either the student or the AI."""

    class Role(models.TextChoices):
        STUDENT = 'student', 'Student'
        AI = 'ai', 'AI'

    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    student_session = models.ForeignKey(
        StudentSession,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        preview = (self.content[:50] + '…') if len(self.content) > 50 else self.content
        return f'{self.get_role_display()}: {preview}'


class Flag(models.Model):
    """A student message auto-flagged as a possible jailbreak attempt.

    Surfaced to the teacher dashboard for review. This is a secondary,
    detection-only feature — it never enforces AI behavior (see ai_client.py).
    """

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name='flags',
    )
    reason = models.CharField(max_length=50, default='keyword_match')
    matched_text = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed = models.BooleanField(default=False)

    def __str__(self):
        return f'Flag ({self.reason}) on message {self.message_id}'
