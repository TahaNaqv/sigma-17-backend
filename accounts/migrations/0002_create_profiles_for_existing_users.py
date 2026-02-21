from django.conf import settings
from django.db import migrations


def create_profiles(apps, schema_editor):
    User = apps.get_model(settings.AUTH_USER_MODEL)
    UserProfile = apps.get_model("accounts", "UserProfile")
    for user in User.objects.all():
        UserProfile.objects.get_or_create(
            user=user,
            defaults={"status": "active" if user.is_active else "inactive"},
        )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_profiles, noop),
    ]
