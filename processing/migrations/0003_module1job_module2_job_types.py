from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("processing", "0002_module1job_uw_parameters"),
    ]

    operations = [
        migrations.AlterField(
            model_name="module1job",
            name="job_type",
            field=models.CharField(
                choices=[
                    ("summary", "Summary"),
                    ("policy_upr", "Policy UPR"),
                    ("update_reserve", "Update Reserve"),
                    ("uw_parameters", "UW Parameters"),
                    ("module2_allocate", "Module2 Allocate"),
                    ("module2_process", "Module2 Process"),
                ],
                max_length=32,
            ),
        ),
    ]
