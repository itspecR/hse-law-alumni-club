from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0015_news_cover_origin")]
    operations = [
        migrations.AddField(model_name="newssourceruns", name="added", field=models.IntegerField(db_default=0)),
    ]
