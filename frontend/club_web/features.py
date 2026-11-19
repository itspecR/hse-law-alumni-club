CHANGES_ENABLED = False
PUSH_ENABLED = False
MINI_APP_ENABLED = False
DPO_ENABLED = False
MINI_APP_PATH = "/tg"
DPO_PATH = "/dpo"
HOME_PATH = "/"
CHANGES_PATH = "/changes"


def section_enabled(href):
    return CHANGES_ENABLED or not href.startswith(CHANGES_PATH)


def mini_app_closed(path):
    return not MINI_APP_ENABLED and path == MINI_APP_PATH


def dpo_closed(path):
    return not DPO_ENABLED and (path == DPO_PATH or path.startswith(DPO_PATH + "/"))


def closed(path):
    return mini_app_closed(path) or dpo_closed(path)


def link_shown(href):
    return section_enabled(href) and not dpo_closed(href)
