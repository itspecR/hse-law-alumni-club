from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("club_data", "0010_promo_codes_foreign_key_actions")]
    operations = [
        migrations.AddField(model_name="orders", name="office_discount", field=models.IntegerField(null=True)),
        migrations.AddField(
            model_name="orders", name="office_reason", field=models.CharField(max_length=300, null=True)
        ),
    ]
