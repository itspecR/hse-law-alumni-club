CHANGES_ENABLED = False
PUSH_ENABLED = False
MINI_APP_ENABLED = False
MINI_APP_PATH = "/tg"
HOME_PATH = "/"
CHANGES_PATH = "/changes"


def section_enabled(href):
    return CHANGES_ENABLED or not href.startswith(CHANGES_PATH)


def mini_app_closed(path):
    return not MINI_APP_ENABLED and path == MINI_APP_PATH
