import nh3
from markdown_it import MarkdownIt
from markupsafe import Markup

from club_web.formatting import media

TAGS = {"p", "br", "strong", "em", "a", "ul", "ol", "li", "h2", "h3", "h4", "blockquote", "hr", "img"}
ATTRIBUTES = {"a": {"href"}, "img": {"src", "alt"}}
URL_SCHEMES = {"http", "https", "mailto"}
FIRST_HEADING, LAST_HEADING = 2, 4
MEDIA_PREFIX = "/api/media/"
PARSER = MarkdownIt("commonmark", {"breaks": True, "html": False}).disable(
    ["code", "fence", "html_block", "backticks", "html_inline"]
)


def heading_tag(tag):
    return "h" + str(min(max(int(tag[1]), FIRST_HEADING), LAST_HEADING))


def own_image(token):
    source = media(token.attrGet("src") or "")
    if not source.startswith(MEDIA_PREFIX):
        return False
    token.attrSet("src", source)
    return True


def adjust(tokens):
    for token in tokens:
        if token.type in ("heading_open", "heading_close"):
            token.tag = heading_tag(token.tag)
        if token.children:
            token.children = [child for child in token.children if child.type != "image" or own_image(child)]
    return tokens


def rich_text(value):
    if not value:
        return Markup("")
    html = PARSER.renderer.render(adjust(PARSER.parse(value)), PARSER.options, {})
    return Markup(nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, url_schemes=URL_SCHEMES))
