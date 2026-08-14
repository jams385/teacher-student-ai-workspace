from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """An AI chat room a teacher creates for a class."""

    class Mode(models.TextChoices):
        SOCRATIC = 'socratic', 'Socratic Mode'
        HOMEWORK = 'homework', 'Homework Mode'
        LECTURE = 'lecture', 'Lecture Mode'

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='workspaces',
    )
    name = models.CharField(max_length=255)
    mode = models.CharField(max_length=20, choices=Mode.choices)
    join_code = models.CharField(max_length=16, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Lecture Mode only: a whole-deck outline generated on teacher demand
    # from all of the workspace's materials pooled together (same pooling
    # send_message() already does for course_material_context) — a static
    # snapshot shown to students for the session, not live-synced to
    # whichever slide is currently on screen. generated_at doubles as the
    # "has an outline been generated yet" flag.
    lecture_outline = models.TextField(blank=True, default='')
    lecture_outline_generated_at = models.DateTimeField(null=True, blank=True)
    # Live Slideshow (Lecture Mode only): which material is currently being
    # presented, and which of its slides the teacher is on. live_material is
    # None whenever nothing is being presented — keep both fields in sync in
    # every view that touches them. SET_NULL (not CASCADE) is required here:
    # the FK points *from* Workspace *to* Material, so CASCADE would delete
    # the Workspace itself if its live Material were deleted.
    live_material = models.ForeignKey(
        'Material', on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )
    live_slide_index = models.PositiveIntegerField(null=True, blank=True, default=None)

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


class Slide(models.Model):
    """One rasterized page of a presentable (PDF-only — see utils.rasterize_pdf)
    Material, used by the Live Slideshow feature. A Material with no Slide
    rows simply isn't eligible to be presented (PPTX materials, or PDFs
    where rasterization failed) — no separate eligibility flag needed."""

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='slides')
    index = models.PositiveIntegerField()  # 0-based page number within the deck
    image = models.CharField(max_length=500)  # Supabase Storage path to the rendered PNG

    class Meta:
        ordering = ['index']
        constraints = [
            models.UniqueConstraint(fields=['material', 'index'], name='unique_slide_index_per_material'),
        ]

    def __str__(self):
        return f'Slide {self.index} of material {self.material_id}'


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
