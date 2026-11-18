from django.db import migrations

OLD_SLOGAN = {"title_pre": "Свои люди", "title_accent": "после выпуска."}
NEW_SLOGAN = {"title_pre": "Традиции, проекты, люди.", "title_accent": None}


def replace_slogan(apps, old, new):
    apps.get_model("club_data", "BlockHero").objects.filter(**old).update(**new)


def forward(apps, schema_editor):
    replace_slogan(apps, OLD_SLOGAN, NEW_SLOGAN)


def backward(apps, schema_editor):
    replace_slogan(apps, {"title_pre": NEW_SLOGAN["title_pre"], "title_accent__isnull": True}, OLD_SLOGAN)


class Migration(migrations.Migration):
    dependencies = [("club_data", "0033_campaign_trainer_roles")]
    operations = [migrations.RunPython(forward, backward)]
