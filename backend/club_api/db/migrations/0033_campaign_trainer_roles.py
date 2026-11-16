from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0032_page_texts")]
    operations = [
        migrations.AddField(
            model_name="campaigntrainers",
            name="role",
            field=models.CharField(db_default="speaker", max_length=20),
        ),
    ]
