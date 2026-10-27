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
    dependencies = [("club_data", "0008_portfolio_applications_foreign_key_actions")]
    operations = [
        migrations.CreateModel(
            name="PromoCodes",
            fields=[
                ("id", uuid_field()),
                ("code", models.CharField(max_length=32, unique=True)),
                ("percent", models.IntegerField()),
                ("starts_on", models.DateField()),
                ("ends_on", models.DateField()),
                ("total_limit", models.IntegerField(null=True)),
                ("per_person_limit", models.IntegerField(null=True)),
                ("all_programs", models.BooleanField(db_default=True)),
                ("status", models.CharField(db_default="active", max_length=32)),
                ("note", models.TextField(null=True)),
                ("created_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={"db_table": "promo_codes"},
        ),
        migrations.CreateModel(
            name="PromoCodePrograms",
            fields=[
                ("id", uuid_field()),
                (
                    "promo",
                    models.ForeignKey(
                        db_column="promo_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="club_data.promocodes",
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
            ],
            options={
                "db_table": "promo_code_programs",
                "constraints": [
                    models.UniqueConstraint(fields=("promo", "program"), name="uq_promo_code_programs_promo_program"),
                ],
            },
        ),
        migrations.AddField(
            model_name="orders",
            name="promo",
            field=models.ForeignKey(
                db_column="promo_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="club_data.promocodes",
            ),
        ),
        migrations.AddField(model_name="orders", name="promo_code", field=models.CharField(max_length=32, null=True)),
        migrations.AddField(
            model_name="orders", name="discount_source", field=models.CharField(max_length=32, null=True)
        ),
    ]
