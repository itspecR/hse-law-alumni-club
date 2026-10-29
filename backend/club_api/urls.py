from django.urls import path

import club_api.modules.analytics.pageviews as analytics_pageviews
import club_api.modules.auth.routes as auth_routes
import club_api.modules.catalog.sync as catalog_sync
import club_api.modules.checkout.admin as checkout_admin
import club_api.modules.checkout.cart as checkout_cart
import club_api.modules.checkout.orders as checkout_orders
import club_api.modules.checkout.payments as checkout_payments
import club_api.modules.content.admin as content_admin
import club_api.modules.content.routes as content_routes
import club_api.modules.events.routes as events_routes
import club_api.modules.gamification.routes as gamification_routes
import club_api.modules.media.routes as media_routes
import club_api.modules.members.admin as members_admin
import club_api.modules.members.community as members_community
import club_api.modules.members.profile as members_profile
import club_api.modules.news.routes as news_routes
import club_api.modules.notifications.push as notifications_push
import club_api.modules.office.routes as office_routes
import club_api.modules.podcasts.routes as podcasts_routes
import club_api.modules.portfolio.admin as portfolio_admin
import club_api.modules.portfolio.applications as portfolio_applications
import club_api.modules.portfolio.captcha as portfolio_captcha
import club_api.modules.portfolio.cohorts as portfolio_cohorts
import club_api.modules.portfolio.discounts as portfolio_discounts
import club_api.modules.portfolio.promo as portfolio_promo
import club_api.modules.portfolio.routes as portfolio_routes
import club_api.modules.portfolio.transfers as portfolio_transfers
import club_api.modules.support.routes as support_routes
import club_api.modules.telegram.routes as telegram_routes
from club_api.core.api import build_api
from club_api.core.views import endpoint, not_found
from club_api.observability import endpoints

handler404 = not_found

routes = (
    [
        path("health", endpoint({"GET": endpoints.health}), name="health"),
        path("ready", endpoint({"GET": endpoints.ready}), name="ready"),
        path("admin/system-health", endpoint({"GET": office_routes.health})),
        path("admin/overview", endpoint({"GET": office_routes.summary})),
        path("admin/analytics", endpoint({"GET": office_routes.report})),
        path("admin/analytics/export.csv", endpoint({"GET": office_routes.export})),
        path("admin/push/broadcast", endpoint({"POST": office_routes.broadcast})),
        path("admin/audit", endpoint({"GET": office_routes.audit_rows})),
        path("auth/login", endpoint({"POST": auth_routes.alumni_login})),
        path("auth/admin-login", endpoint({"POST": auth_routes.admin_login})),
        path("auth/admin-logout", endpoint({"POST": auth_routes.admin_logout})),
        path("auth/admin-session", endpoint({"GET": auth_routes.admin_session})),
        path("auth/register", endpoint({"POST": auth_routes.register})),
        path("auth/resend-confirmation", endpoint({"POST": auth_routes.resend_confirmation})),
        path("auth/confirm", endpoint({"POST": auth_routes.confirm})),
        path("auth/forgot", endpoint({"POST": auth_routes.forgot})),
        path("auth/reset", endpoint({"POST": auth_routes.reset})),
        path("auth/telegram", endpoint({"POST": auth_routes.telegram_login})),
        path("points", endpoint({"POST": gamification_routes.points})),
        path("decay/run", endpoint({"POST": gamification_routes.decay})),
        path("me/ledger", endpoint({"GET": gamification_routes.ledger})),
        path("admin/dpo-sync", endpoint({"POST": catalog_sync.sync})),
        path("admin/pages/<str:slug>", endpoint({"GET": content_admin.admin_page, "PATCH": content_admin.patch_page})),
        path("robots.txt", endpoint({"GET": content_routes.robots})),
        path("sitemap.xml", endpoint({"GET": content_routes.sitemap})),
        path("news", endpoint({"GET": content_routes.news})),
        path("news/<str:slug>", endpoint({"GET": content_routes.news_detail})),
        path("programs", endpoint({"GET": content_routes.programs})),
        path("programs/<str:slug>", endpoint({"GET": content_routes.program_detail})),
        path("products", endpoint({"GET": content_routes.products})),
        path("portfolio", endpoint({"GET": portfolio_routes.programs})),
        path("portfolio/challenge", endpoint({"GET": portfolio_captcha.challenge})),
        path("portfolio/<str:slug>", endpoint({"GET": portfolio_routes.program})),
        path("portfolio/<str:slug>/applications", endpoint({"POST": portfolio_applications.apply})),
        path("portfolio/<str:slug>/quote", endpoint({"POST": portfolio_applications.quote})),
        path("timeline", endpoint({"GET": content_routes.timeline})),
        path("pages/<str:slug>", endpoint({"GET": content_routes.page})),
        path(
            "cart",
            endpoint(
                {
                    "GET": checkout_cart.cart,
                    "POST": checkout_cart.add_cart,
                    "PATCH": checkout_cart.change_qty,
                    "DELETE": checkout_cart.clear_cart,
                }
            ),
        ),
        path("payments/config", endpoint({"GET": checkout_payments.payment_config})),
        path("orders/<str:number>/pay", endpoint({"POST": checkout_payments.pay})),
        path("payments/yookassa/webhook", endpoint({"POST": checkout_payments.webhook})),
        path("orders", endpoint({"POST": checkout_orders.create_order})),
        path("me/orders", endpoint({"GET": checkout_orders.my_orders})),
        path("admin/orders", endpoint({"GET": checkout_admin.orders})),
        path("admin/orders/<str:id>", endpoint({"PATCH": checkout_admin.patch_order})),
        path("admin/orders/<str:id>/discount", endpoint({"PATCH": portfolio_discounts.set_discount})),
        path("admin/orders/<str:id>/cohort", endpoint({"PATCH": portfolio_transfers.move_order})),
        path("admin/orders/export.csv", endpoint({"GET": checkout_admin.export_orders})),
        path("podcasts", endpoint({"GET": podcasts_routes.podcasts})),
        path("podcasts/<str:id>/audio", endpoint({"GET": podcasts_routes.audio})),
        path("podcasts/subscribe", endpoint({"POST": podcasts_routes.subscribe})),
        path("admin/podcast-subs", endpoint({"GET": podcasts_routes.subscribers})),
        path("admin/news-sources", endpoint({"GET": news_routes.sources})),
        path("admin/news-sources/refresh", endpoint({"POST": news_routes.refresh})),
        path("admin/news-sources/settings", endpoint({"PATCH": news_routes.settings})),
        path("admin/news-sources/<str:id>", endpoint({"PATCH": news_routes.patch})),
        path("support/config", endpoint({"GET": support_routes.config})),
        path("support/faq-event", endpoint({"POST": support_routes.faq_event})),
        path("support/ask", endpoint({"POST": support_routes.ask})),
        path("support", endpoint({"POST": support_routes.create_ticket})),
        path("support/<str:id>", endpoint({"GET": support_routes.ticket, "DELETE": support_routes.delete_ticket})),
        path("support/<str:id>/messages", endpoint({"POST": support_routes.message})),
        path("admin/support", endpoint({"GET": support_routes.admin_tickets})),
        path("admin/support/<str:id>", endpoint({"PATCH": support_routes.answer_ticket})),
        path("admin/bot-status", endpoint({"GET": support_routes.bot_status})),
        path("telegram/webhook", endpoint({"POST": telegram_routes.webhook})),
        path("me/tg-link", endpoint({"GET": members_profile.telegram_link})),
        path("me", endpoint({"GET": members_profile.me})),
        path("me/profile", endpoint({"PATCH": members_profile.profile})),
        path("me/export", endpoint({"GET": members_profile.export})),
        path("me/delete", endpoint({"POST": members_profile.delete_me})),
        path("admin/members", endpoint({"GET": members_admin.members})),
        path("admin/members/<str:id>/podcast-sub", endpoint({"POST": members_admin.grant_subscription})),
        path("admin/members/<str:id>", endpoint({"PATCH": members_admin.patch_member})),
        path("admin/members/<str:id>/points", endpoint({"POST": members_admin.points})),
        path("admin/members/<str:id>/anonymize", endpoint({"POST": members_admin.erase})),
        path("me/classmates", endpoint({"GET": members_community.classmates})),
        path("me/events", endpoint({"GET": members_community.notifications})),
        path("me/friends", endpoint({"POST": members_community.friend})),
        path("me/friends/<str:alumniId>", endpoint({"DELETE": members_community.remove_friend})),
        path("stats", endpoint({"GET": events_routes.stats})),
        path("events", endpoint({"GET": events_routes.events})),
        path("events/<str:id>.ics", endpoint({"GET": events_routes.calendar})),
        path("events/<str:id>/rsvp", endpoint({"POST": events_routes.rsvp})),
        path("admin/events", endpoint({"GET": events_routes.admin_events, "POST": events_routes.create_event})),
        path("admin/events/<str:id>/rsvps", endpoint({"GET": events_routes.admin_roster})),
        path(
            "admin/events/<str:id>",
            endpoint({"PATCH": events_routes.patch_event, "DELETE": events_routes.delete_event}),
        ),
        path("admin/events/rsvp/<str:rsvpId>/attend", endpoint({"POST": events_routes.attend})),
        path("push/vapid", endpoint({"GET": notifications_push.vapid})),
        path("me/push/subscribe", endpoint({"POST": notifications_push.subscribe})),
        path("me/push/unsubscribe", endpoint({"POST": notifications_push.unsubscribe})),
        path("analytics/pageview", endpoint({"POST": analytics_pageviews.pageview})),
        path("media/<str:fileId>", endpoint({"GET": media_routes.public_media})),
        path("admin/media", endpoint({"GET": media_routes.media_list, "POST": media_routes.upload_office})),
        path("admin/media/<str:fileId>/content", endpoint({"GET": media_routes.office_content})),
        path("admin/media/<str:fileId>", endpoint({"DELETE": media_routes.delete_media})),
        path("me/avatar", endpoint({"POST": media_routes.upload_avatar})),
        path("avatars/<str:fileId>", endpoint({"GET": media_routes.avatar})),
    ]
    + content_admin.urlpatterns
    + portfolio_admin.urlpatterns
    + portfolio_promo.urlpatterns
    + portfolio_cohorts.urlpatterns
    + podcasts_routes.urlpatterns
)
routes.sort(key=lambda route: str(route.pattern).count("<"))
api = build_api(routes)
urlpatterns = [path("", api.urls)]
