import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import club_api.db.functions


class Migration(migrations.Migration):
    dependencies = [("club_data", "0020_mailing_consent")]
    operations = [
        migrations.CreateModel(
            name="Donations",
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
                ("number", models.CharField(max_length=32, unique=True)),
                ("amount", models.BigIntegerField()),
                ("period", models.CharField(db_default="once", max_length=16)),
                ("email", models.CharField(max_length=254)),
                ("name", models.CharField(max_length=200, null=True)),
                ("status", models.CharField(db_default="new", max_length=16)),
                ("payment_id", models.CharField(max_length=64, null=True)),
                ("paid_at", models.DateTimeField(null=True)),
                ("consents", models.JSONField(null=True)),
                ("created_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                (
                    "alumni",
                    models.ForeignKey(
                        db_column="alumni_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="club_data.alumnus",
                    ),
                ),
            ],
            options={
                "db_table": "club_donations",
                "indexes": [models.Index(fields=["status", "created_at"], name="club_donations_status_created")],
            },
        ),
    ]
