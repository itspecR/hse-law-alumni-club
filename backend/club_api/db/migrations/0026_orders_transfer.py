from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("club_data", "0025_donations_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="orders",
            name="transfer_sent_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddField(
            model_name="orders",
            name="transfer_token",
            field=models.CharField(max_length=64, null=True, unique=True),
        ),
    ]
