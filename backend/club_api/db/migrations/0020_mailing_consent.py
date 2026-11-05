from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0019_email_codes")]
    operations = [
        migrations.AddField(model_name="alumnus", name="mailing_consent_at", field=models.DateTimeField(null=True)),
        migrations.AddField(
            model_name="alumnus", name="mailing_consent_version", field=models.CharField(max_length=32, null=True)
        ),
    ]
