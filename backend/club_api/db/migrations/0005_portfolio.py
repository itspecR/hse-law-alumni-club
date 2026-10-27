import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import club_api.db.functions


def uuid_field():
    return models.UUIDField(
        db_default=club_api.db.functions.DatabaseUUID(), default=uuid.uuid4, primary_key=True, serialize=False
    )


class Migration(migrations.Migration):
    dependencies = [("club_data", "0004_mail_outbox_owner")]
    operations = [
        migrations.AddField(
            model_name="programs", name="catalog", field=models.CharField(db_default="dpo", max_length=32)
        ),
        migrations.AddField(model_name="programs", name="competencies", field=models.JSONField(null=True)),
        migrations.AddField(model_name="programs", name="communication", field=models.JSONField(null=True)),
        migrations.AddConstraint(
            model_name="programs",
            constraint=models.CheckConstraint(
                condition=models.Q(("catalog__in", ("dpo", "portfolio"))), name="programs_catalog_check"
            ),
        ),
        migrations.CreateModel(
            name="Trainers",
            fields=[
                ("id", uuid_field()),
                ("fio", models.CharField(max_length=255)),
                ("photo", models.CharField(max_length=255, null=True)),
                ("bio", models.TextField(null=True)),
                ("status", models.CharField(db_default="draft", max_length=255)),
                ("created_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={"db_table": "trainers"},
        ),
        migrations.CreateModel(
            name="ProgramTrainers",
            fields=[
                ("id", uuid_field()),
                (
                    "program",
                    models.ForeignKey(
                        db_column="program_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="club_data.programs",
                    ),
                ),
                (
                    "trainer",
                    models.ForeignKey(
                        db_column="trainer_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="club_data.trainers",
                    ),
                ),
                ("sort", models.IntegerField(db_default=0)),
            ],
            options={
                "db_table": "program_trainers",
                "constraints": [
                    models.UniqueConstraint(fields=("program", "trainer"), name="uq_program_trainers_program_trainer"),
                ],
            },
        ),
    ]
