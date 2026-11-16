from django.db import migrations, models
from django.db.models import F

DATED_MODELS = ("Campaigns", "Events", "News", "Podcasts", "Products", "Programs")
FIRST_DATES = {"Campaigns": "created_at", "Events": "created_at", "Podcasts": "created_at", "News": "published_at"}


def fill_dates(apps, schema_editor):
    for name, source in FIRST_DATES.items():
        apps.get_model("club_data", name).objects.filter(updated_at=None).update(updated_at=F(source))


class Migration(migrations.Migration):
    dependencies = [("club_data", "0030_event_podcast_slugs")]
    operations = [
        *(
            migrations.AddField(model_name=name.lower(), name="updated_at", field=models.DateTimeField(null=True))
            for name in DATED_MODELS
        ),
        migrations.RunPython(fill_dates, migrations.RunPython.noop),
    ]
