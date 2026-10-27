from django.db import migrations

from club_api.db.foreign_keys import apply_actions


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("club_data", "0009_promo_codes")]
    operations = [migrations.RunPython(apply_actions)]
