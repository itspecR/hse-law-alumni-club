from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0018_dpo_sync")]
    operations = [
        migrations.CreateModel(
            name="EmailCodes",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("user_id", models.UUIDField()),
                ("purpose", models.CharField(max_length=32)),
                ("code_hash", models.CharField(max_length=64, null=True)),
                ("attempts", models.IntegerField(db_default=0)),
                ("expires_at", models.DateTimeField(null=True)),
                ("sent_at", models.DateTimeField()),
                ("window_started_at", models.DateTimeField()),
                ("window_count", models.IntegerField(db_default=0)),
            ],
            options={
                "db_table": "club_email_codes",
                "constraints": [
                    models.UniqueConstraint(fields=("user_id", "purpose"), name="club_email_codes_user_purpose")
                ],
            },
        ),
    ]
