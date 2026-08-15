import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0005_backfill_teacher_profiles'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='studentsession',
            name='student',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='student_sessions', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='studentsession',
            name='session_id',
            field=models.CharField(blank=True, max_length=64, null=True, unique=True),
        ),
        migrations.AddConstraint(
            model_name='studentsession',
            constraint=models.UniqueConstraint(condition=models.Q(('student__isnull', False)), fields=('student', 'workspace'), name='unique_student_workspace_when_authenticated'),
        ),
    ]
