import importlib

slogan = importlib.import_module("club_api.db.migrations.0034_home_slogan")


class Rows:
    def __init__(self, log):
        self.log = log

    def filter(self, **found):
        self.log.append(found)
        return self

    def update(self, **values):
        self.log.append(values)


class Apps:
    def __init__(self):
        self.log = []

    def get_model(self, app, name):
        assert (app, name) == ("club_data", "BlockHero")
        return type("BlockHero", (), {"objects": Rows(self.log)})


def test_only_the_untouched_old_slogan_is_replaced():
    apps = Apps()
    slogan.forward(apps, None)
    assert apps.log == [
        {"title_pre": "Свои люди", "title_accent": "после выпуска."},
        {"title_pre": "Традиции, проекты, люди.", "title_accent": None},
    ]


def test_rollback_returns_the_old_slogan_only_where_the_new_one_stands():
    apps = Apps()
    slogan.backward(apps, None)
    assert apps.log == [
        {"title_pre": "Традиции, проекты, люди.", "title_accent__isnull": True},
        {"title_pre": "Свои люди", "title_accent": "после выпуска."},
    ]
