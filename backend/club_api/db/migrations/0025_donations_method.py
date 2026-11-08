from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("club_data", "0024_fiscal_receipts_foreign_key_actions"),
    ]

    operations = [
        migrations.AddField(
            model_name="donations",
            name="method",
            field=models.CharField(db_default="card", max_length=16),
        ),
    ]
