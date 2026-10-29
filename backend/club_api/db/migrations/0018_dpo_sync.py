import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0017_news_settings")]
    operations = [
        migrations.AddField(
            model_name="programs", name="cover_source", field=models.CharField(max_length=500, null=True)
        ),
        migrations.CreateModel(
            name="DpoSync",
            fields=[
                ("id", models.SmallIntegerField(primary_key=True, serialize=False)),
                ("checked_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ("found", models.IntegerField(db_default=0)),
                ("created", models.IntegerField(db_default=0)),
                ("updated", models.IntegerField(db_default=0)),
                ("archived", models.IntegerField(db_default=0)),
                ("failures", models.IntegerField(db_default=0)),
                ("error", models.TextField(null=True)),
            ],
            options={
                "db_table": "club_dpo_sync",
                "constraints": [models.CheckConstraint(condition=models.Q(id=1), name="club_dpo_sync_single_row")],
            },
        ),
    ]
