from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0011_office_discount")]
    operations = [
        migrations.CreateModel(
            name="CaptchaUses",
            fields=[
                ("salt_hash", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("expires_at", models.DateTimeField()),
            ],
            options={
                "db_table": "club_captcha_uses",
                "indexes": [models.Index(fields=["expires_at"], name="club_captcha_uses_expires_idx")],
            },
        ),
    ]
