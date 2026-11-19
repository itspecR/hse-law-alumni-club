from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0034_home_slogan")]
    operations = [migrations.AddField(model_name="pages", name="sections", field=models.JSONField(null=True))]
