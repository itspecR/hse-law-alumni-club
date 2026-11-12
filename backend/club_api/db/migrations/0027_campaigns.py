import uuid

import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

import club_api.db.functions


class Migration(migrations.Migration):
    dependencies = [
        ("club_data", "0026_orders_transfer"),
    ]

    operations = [
        migrations.CreateModel(
            name="Campaigns",
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
                ("slug", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(db_default="draft", max_length=16)),
                ("title", models.CharField(max_length=255)),
                ("subtitle", models.CharField(max_length=255, null=True)),
                ("lede", models.TextField(null=True)),
                ("date_text", models.CharField(max_length=255, null=True)),
                ("time_text", models.CharField(max_length=255, null=True)),
                ("place_text", models.CharField(max_length=255, null=True)),
                ("duration_text", models.CharField(max_length=255, null=True)),
                ("goal_title", models.CharField(max_length=255, null=True)),
                ("goal_text", models.TextField(null=True)),
                ("goal_amount", models.BigIntegerField(db_default=0)),
                ("extra_amount", models.BigIntegerField(db_default=0)),
                ("invite_threshold", models.BigIntegerField(db_default=0)),
                ("amounts", models.JSONField(null=True)),
                ("about_title", models.CharField(max_length=255, null=True)),
                ("about", models.TextField(null=True)),
                ("highlights", models.JSONField(null=True)),
                ("program_title", models.CharField(max_length=255, null=True)),
                ("modules", models.JSONField(null=True)),
                ("feature_title", models.CharField(max_length=255, null=True)),
                ("feature_note", models.CharField(max_length=255, null=True)),
                ("feature_text", models.TextField(null=True)),
                ("results_title", models.CharField(max_length=255, null=True)),
                ("results", models.JSONField(null=True)),
                ("steps", models.JSONField(null=True)),
                ("faq", models.JSONField(null=True)),
                ("note", models.TextField(null=True)),
                ("payment_purpose", models.CharField(max_length=140, null=True)),
                ("invite_subject", models.CharField(max_length=255, null=True)),
                ("invite_body", models.TextField(null=True)),
                ("cover", models.CharField(max_length=255, null=True)),
                (
                    "created_at",
                    models.DateTimeField(db_default=django.db.models.functions.datetime.Now()),
                ),
            ],
            options={
                "db_table": "club_campaigns",
            },
        ),
        migrations.AddField(
            model_name="donations",
            name="invited_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="donations",
            name="campaign",
            field=models.ForeignKey(
                db_column="campaign_id",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="club_data.campaigns",
            ),
        ),
        migrations.CreateModel(
            name="CampaignTrainers",
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
                ("sort", models.IntegerField(db_default=0)),
                (
                    "campaign",
                    models.ForeignKey(
                        db_column="campaign_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="club_data.campaigns",
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
            ],
            options={
                "db_table": "club_campaign_trainers",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("campaign", "trainer"),
                        name="uq_club_campaign_trainers_pair",
                    )
                ],
            },
        ),
    ]
