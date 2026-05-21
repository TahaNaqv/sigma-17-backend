"""Chaining + retention + lineage.

Additive, nullable columns + a self-FK + new indexes. Zero downtime.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("processing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="module1job",
            name="source_job",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="derived_jobs",
                to="processing.module1job",
            ),
        ),
        migrations.AddField(
            model_name="module1job",
            name="source_artifact",
            field=models.CharField(blank=True, max_length=128),
        ),
        migrations.AddField(
            model_name="module1job",
            name="output_artifacts",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="module1job",
            name="retention_until",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="module1job",
            name="legal_hold",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="module1job",
            name="output_purged_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="module1job",
            index=models.Index(
                fields=["organization", "job_type", "status", "-created_at"],
                name="m1job_org_type_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="module1job",
            index=models.Index(
                fields=["retention_until", "legal_hold"],
                name="m1job_retention_sweep_idx",
            ),
        ),
    ]
