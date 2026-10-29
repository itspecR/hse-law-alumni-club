import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0016_news_source_runs_added")]
    operations = [
        migrations.AddField(model_name="newssourceruns", name="failures", field=models.IntegerField(db_default=0)),
        migrations.CreateModel(
            name="NewsSettings",
            fields=[
                ("id", models.SmallIntegerField(primary_key=True, serialize=False)),
                ("enabled", models.BooleanField(db_default=False)),
                ("interval_hours", models.IntegerField(db_default=3)),
                ("updated_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
            ],
            options={
                "db_table": "club_news_settings",
                "constraints": [
                    models.CheckConstraint(condition=models.Q(id=1), name="club_news_settings_single_row"),
                    models.CheckConstraint(
                        condition=models.Q(interval_hours__in=[1, 3, 6, 12, 24]),
                        name="club_news_settings_interval_check",
                    ),
                ],
            },
        ),
    ]
