import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0006_portfolio_foreign_key_actions")]
    operations = [
        migrations.AddField(
            model_name="orders",
            name="program",
            field=models.ForeignKey(
                db_column="program_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="club_data.programs",
            ),
        ),
        migrations.AddField(model_name="orders", name="participants", field=models.IntegerField(db_default=1)),
        migrations.AddField(
            model_name="orders", name="organization", field=models.CharField(max_length=300, null=True)
        ),
        migrations.AddField(model_name="orders", name="consents", field=models.JSONField(null=True)),
    ]
