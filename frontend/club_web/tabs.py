from club_web import features

TABS = (
    ("/", "Клуб", "home"),
    ("/news", "Новости", "news"),
    ("/dpo", "ДПО", "portfolio"),
    ("/merch", "Мерч", "products"),
    ("/lk", "Кабинет", "profile"),
)
TABS_WITHOUT_DPO = (
    ("/", "Клуб", "home"),
    ("/portfolio", "Тренинги", "programs"),
    ("/merch", "Мерч", "products"),
    ("/lk", "Кабинет", "profile"),
)
HOME_TAB_PATHS = (features.HOME_PATH, features.MINI_APP_PATH)


def tab_current(href, path):
    if href == features.HOME_PATH:
        return path in HOME_TAB_PATHS
    return path == href or path.startswith(href + "/")


def mobile_tabs(path):
    tabs = TABS if features.DPO_ENABLED else TABS_WITHOUT_DPO
    return [(href, label, icon, tab_current(href, path)) for href, label, icon in tabs]
