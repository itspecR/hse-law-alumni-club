import re

PUBLIC_READING_PATH = re.compile(r"/(?:news|dpo|portfolio|merch|events|changes)(?:/[^/]+)?\Z")
PWA_PARAMS = {"source": "pwa", "pwa": "1", "site": "1"}


def can_save_page(path, params, data, *, fragment, authorized):
    return (
        not fragment
        and not authorized
        and data["status"] == 200
        and not data["errors"]
        and all(PWA_PARAMS.get(key) == value for key, value in params.items())
        and (path in {"/", "/tg", "/saved"} or PUBLIC_READING_PATH.fullmatch(path) is not None)
    )
