CHANGES_ENABLED = False
PUSH_ENABLED = False
CHANGES_PATH = "/changes"


def section_enabled(href):
    return CHANGES_ENABLED or not href.startswith(CHANGES_PATH)
