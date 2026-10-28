import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import club_api.db.functions


class Migration(migrations.Migration):
    dependencies = [("club_data", "0012_captcha_uses")]
    operations = [
        migrations.CreateModel(
            name="ProgramCohorts",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        db_default=club_api.db.functions.DatabaseUUID(),
                        default=uuid.uuid4,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "program",
                    models.ForeignKey(
                        db_column="program_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="club_data.programs",
                    ),
                ),
                ("starts_on", models.DateField()),
                ("price", models.BigIntegerField(null=True)),
                ("capacity", models.IntegerField(null=True)),
                ("enrollment", models.CharField(db_default="actual", max_length=32)),
                ("created_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={"db_table": "program_cohorts"},
        ),
        migrations.AddField(
            model_name="orders",
            name="cohort",
            field=models.ForeignKey(
                db_column="cohort_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="club_data.programcohorts",
            ),
        ),
    ]
