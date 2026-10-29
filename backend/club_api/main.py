import logging
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx

from club_api.asgi import Application
from club_api.core.config import load_settings
from club_api.core.http import SecurityMiddleware
from club_api.db.pool import Database
from club_api.db.store import Store
from club_api.domain import DOMAIN
from club_api.jobs.runner import Jobs
from club_api.modules.analytics.pageviews import ROUTE_LIMITS as ANALYTICS_LIMITS
from club_api.modules.auth.routes import ROUTE_LIMITS
from club_api.modules.auth.service import AuthService
from club_api.modules.catalog.center import ROUTE_LIMITS as SYNC_LIMITS
from club_api.modules.checkout.cart import ROUTE_LIMITS as CART_LIMITS
from club_api.modules.checkout.orders import ROUTE_LIMITS as ORDER_LIMITS
from club_api.modules.checkout.payments import ROUTE_LIMITS as PAYMENT_LIMITS
from club_api.modules.checkout.payments import Payments
from club_api.modules.checkout.store import Checkout
from club_api.modules.events.routes import ROUTE_LIMITS as EVENT_LIMITS
from club_api.modules.gamification.service import Gamification
from club_api.modules.media.routes import ROUTE_LIMITS as MEDIA_LIMITS
from club_api.modules.media.service import Media
from club_api.modules.members.community import ROUTE_LIMITS as COMMUNITY_LIMITS
from club_api.modules.members.profile import ROUTE_LIMITS as PROFILE_LIMITS
from club_api.modules.news.routes import ROUTE_LIMITS as NEWS_LIMITS
from club_api.modules.notifications.mail import Notifications
from club_api.modules.notifications.push import ROUTE_LIMITS as PUSH_LIMITS
from club_api.modules.notifications.push import Push
from club_api.modules.office.routes import ROUTE_LIMITS as OFFICE_LIMITS
from club_api.modules.podcasts.routes import ROUTE_LIMITS as PODCAST_LIMITS
from club_api.modules.portfolio.applications import ROUTE_LIMITS as PORTFOLIO_LIMITS
from club_api.modules.support.routes import ROUTE_LIMITS as SUPPORT_LIMITS
from club_api.modules.telegram.bot import Telegram
from club_api.modules.telegram.routes import ROUTE_LIMITS as TELEGRAM_LIMITS

logger = logging.getLogger("club.api")


def create_app(settings=None):
    settings = settings or load_settings()
    if errors := settings.production_errors():
        raise RuntimeError("; ".join(errors))
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(app):
        await database.open()
        try:
            if database.pool:
                await database.execute("DELETE FROM club_auth_revocations WHERE expires_at <= now()")
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                app.state.client = client
                app.state.notifications = Notifications(settings, database, client)
                await app.state.jobs.start()
                try:
                    yield
                finally:
                    await app.state.jobs.stop()
        finally:
            await app.state.media.close()
            await database.close()

    app = Application(SimpleNamespace(), lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.store = Store(database)
    app.state.auth = AuthService(settings, database, app.state.store)
    app.state.domain = DOMAIN
    app.state.started_at = time.monotonic()
    app.state.checkout = Checkout(app.state)
    app.state.payments = Payments(app.state)
    app.state.gamification = Gamification(app.state)
    app.state.push = Push(app.state)
    app.state.media = Media(app.state)
    app.state.telegram = Telegram(app.state)
    app.state.jobs = Jobs(app.state)
    app.handler = SecurityMiddleware(
        app.handler,
        settings=settings,
        route_limits={
            **ROUTE_LIMITS,
            **CART_LIMITS,
            **ORDER_LIMITS,
            **PAYMENT_LIMITS,
            **PUSH_LIMITS,
            **PROFILE_LIMITS,
            **COMMUNITY_LIMITS,
            **EVENT_LIMITS,
            **MEDIA_LIMITS,
            **SUPPORT_LIMITS,
            **PODCAST_LIMITS,
            **PORTFOLIO_LIMITS,
            **OFFICE_LIMITS,
            **ANALYTICS_LIMITS,
            **SYNC_LIMITS,
            **NEWS_LIMITS,
            **TELEGRAM_LIMITS,
        },
    )
    return app


def run():
    from club_api.server import Server

    settings = load_settings()
    from club_api.observability.errors import initialize

    initialize(settings)
    from club_api.observability.logging import JsonFormatter

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    Server(lambda: create_app(settings), settings.API_HOST, settings.API_PORT).run()


if __name__ == "__main__":
    run()
