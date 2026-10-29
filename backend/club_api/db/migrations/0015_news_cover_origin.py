from django.db import migrations, models

FACULTY_URLS = "https://pravo.hse.ru/"


def mark_faculty_news(apps, schema_editor):
    apps.get_model("club_data", "News").objects.filter(source_url__startswith=FACULTY_URLS).update(origin="faculty")


class Migration(migrations.Migration):
    dependencies = [("club_data", "0014_program_cohorts_foreign_key_actions")]
    operations = [
        migrations.AddField(model_name="news", name="cover", field=models.CharField(max_length=500, null=True)),
        migrations.AddField(model_name="news", name="origin", field=models.CharField(db_default="club", max_length=32)),
        migrations.RunPython(mark_faculty_news, migrations.RunPython.noop),
    ]
