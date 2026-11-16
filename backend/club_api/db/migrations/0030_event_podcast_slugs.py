from django.db import migrations, models

from club_api.core.models import slugify

SLUG_MODELS = ("Events", "Podcasts")
ID_PART = 8


def free_slug(title, taken, id):
    slug = slugify(title or str(id))
    return slug + "-" + str(id)[:ID_PART] if slug in taken else slug


def fill_slugs(apps, schema_editor):
    for name in SLUG_MODELS:
        model = apps.get_model("club_data", name)
        taken = set(model.objects.exclude(slug=None).values_list("slug", flat=True))
        for row in model.objects.filter(slug=None).order_by("created_at", "id"):
            row.slug = free_slug(row.title, taken, row.id)
            taken.add(row.slug)
            row.save(update_fields=["slug"])


class Migration(migrations.Migration):
    dependencies = [("club_data", "0029_education_document")]
    operations = [
        migrations.AddField(
            model_name="events", name="slug", field=models.CharField(max_length=255, null=True, unique=True)
        ),
        migrations.AddField(
            model_name="podcasts", name="slug", field=models.CharField(max_length=255, null=True, unique=True)
        ),
        migrations.RunPython(fill_slugs, migrations.RunPython.noop),
    ]
