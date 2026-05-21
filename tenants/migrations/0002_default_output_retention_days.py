from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="default_output_retention_days",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text=(
                    "Output ZIPs become eligible for the daily cleanup sweep "
                    "this many days after the job succeeds. Leave blank to "
                    "retain forever."
                ),
            ),
        ),
    ]
