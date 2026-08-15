from django.db import migrations


def backfill_teacher_profiles(apps, schema_editor):
    """Every User row created before this feature existed is, by definition,
    a teacher — students never had accounts until now. Uses historical
    models (not the real Profile.Role.TEACHER, which historical models
    don't carry) and get_or_create for idempotency on re-run."""
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('workspaces', 'Profile')
    for user in User.objects.all():
        Profile.objects.get_or_create(user=user, defaults={'role': 'teacher'})


def noop_reverse(apps, schema_editor):
    # Deliberately not reversing the backfill (deleting Profile rows on
    # migrate-down would also strip role data from any student accounts
    # created after this migration ran) — reversing 0004's CreateModel
    # already removes the table entirely, which is enough.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('workspaces', '0004_profile_rosterinvite'),
    ]

    operations = [
        migrations.RunPython(backfill_teacher_profiles, noop_reverse),
    ]
