from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0031_record_updated_at")]
    operations = [
        migrations.AddField(model_name="pages", name="body", field=models.TextField(null=True)),
        migrations.AddField(model_name="pages", name="meta_title", field=models.CharField(max_length=255, null=True)),
        migrations.AddField(
            model_name="pages", name="meta_description", field=models.CharField(max_length=300, null=True)
        ),
    ]
