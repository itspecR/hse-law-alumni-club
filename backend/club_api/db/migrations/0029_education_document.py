from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0028_campaigns_foreign_key_actions")]
    operations = [
        migrations.AddField(
            model_name="alumnus", name="education_document", field=models.CharField(max_length=36, null=True)
        ),
    ]
