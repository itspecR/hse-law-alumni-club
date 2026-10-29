import uuid

from django.db import models
from django.db.models.functions import Now

from club_api.db.functions import DatabaseUUID
from club_api.domain import PROGRAM_CATALOGS


class Achievements(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    key = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    description = models.TextField(null=True)
    rule_json = models.JSONField(null=True)
    points_reward = models.IntegerField(null=True, db_default=0)
    sort = models.IntegerField(null=True)
    icon = models.CharField(max_length=255, null=True)
    kind = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "achievements"


class Alumnus(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    user = models.OneToOneField("User", on_delete=models.SET_NULL, db_column="user_id", related_name="+", null=True)
    fio = models.CharField(max_length=255, null=True)
    cohort = models.CharField(max_length=255, null=True)
    status = models.CharField(max_length=255, null=True, db_default="active")
    verification_status = models.CharField(max_length=255, null=True, db_default="pending")
    points_cached = models.IntegerField(null=True, db_default=0)
    level_cached = models.CharField(max_length=255, null=True, db_default="graduate")
    personal_discount = models.IntegerField(null=True, db_default=0)
    contacts_json = models.JSONField(null=True)
    edu_program = models.CharField(max_length=255, null=True)
    edu_level = models.CharField(max_length=255, null=True)
    interests_json = models.JSONField(null=True)
    podcast_reminder_sent = models.BooleanField(null=True, db_default=False)
    podcast_sub_until = models.DateTimeField(null=True)
    avatar = models.CharField(max_length=255, null=True)
    referral_code = models.CharField(max_length=255, unique=True, null=True)
    telegram_id = models.CharField(max_length=255, unique=True, null=True)
    token_version = models.IntegerField(null=True, db_default=0)
    consent_at = models.DateTimeField(null=True)
    consent_version = models.CharField(max_length=255, null=True)
    verified_at = models.DateTimeField(null=True)
    referred_by = models.ForeignKey(
        "Alumnus", on_delete=models.SET_NULL, db_column="referred_by", related_name="+", null=True
    )
    joined_at = models.DateTimeField(null=True, db_default=Now())
    last_activity_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "alumni"
        indexes = [
            models.Index(fields=["verification_status"], name="idx_alumni_verif"),
            models.Index(fields=["cohort"], name="idx_alumni_cohort"),
        ]


class AlumniAchievements(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    achievement = models.ForeignKey(
        "Achievements", on_delete=models.CASCADE, db_column="achievement_id", related_name="+", null=True
    )
    earned_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "alumni_achievements"


class AlumniFriends(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    friend = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="friend_id", related_name="+", null=True)
    status = models.CharField(max_length=255, null=True, db_default="pending")
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "alumni_friends"


class AuditLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    event = models.CharField(max_length=255, null=True)
    actor = models.CharField(max_length=255, null=True)
    subject = models.CharField(max_length=255, null=True)
    detail = models.JSONField(null=True)
    ip = models.CharField(max_length=255, null=True)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["created_at"], name="idx_audit_created"),
        ]


class BlockCta(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    title = models.CharField(max_length=255, null=True)
    text = models.TextField(null=True)
    button = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "block_cta"


class BlockHero(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    badge = models.CharField(max_length=255, null=True)
    title_pre = models.CharField(max_length=255, null=True)
    title_accent = models.CharField(max_length=255, null=True)
    subtitle = models.TextField(null=True)
    cta_primary = models.CharField(max_length=255, null=True)
    cta_secondary = models.CharField(max_length=255, null=True)
    history_eyebrow = models.CharField(max_length=255, null=True)
    history_title = models.CharField(max_length=255, null=True)
    history_hint = models.CharField(max_length=255, null=True)
    marquee = models.JSONField(null=True)

    class Meta:
        db_table = "block_hero"


class Carts(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    alumni = models.ForeignKey("Alumnus", on_delete=models.SET_NULL, db_column="alumni_id", related_name="+", null=True)
    session_token = models.CharField(max_length=255, null=True)
    items_json = models.JSONField(null=True)
    updated_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "carts"
        indexes = [
            models.Index(fields=["session_token"], name="idx_carts_session"),
        ]


class AuthRevocations(models.Model):
    token_key = models.CharField(max_length=255, primary_key=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "club_auth_revocations"
        indexes = [
            models.Index(fields=["expires_at"], name="club_auth_revocations_expiry_i"),
        ]


class CheckoutCommits(models.Model):
    key_hash = models.CharField(max_length=255, primary_key=True)
    request_hash = models.TextField()
    order = models.OneToOneField("Orders", on_delete=models.CASCADE, db_column="order_id", related_name="+")
    reservations = models.JSONField(default=list, db_default=models.Value([], output_field=models.JSONField()))
    released = models.BooleanField(db_default=False)
    receipt = models.JSONField()
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "club_checkout_commits"


class FaqEvents(models.Model):
    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(max_length=255)
    gap_id = models.CharField(max_length=255, null=True)
    channel = models.CharField(max_length=255)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "club_faq_events"
        constraints = [
            models.CheckConstraint(condition=models.Q(kind__in=["gap", "none"]), name="club_faq_events_kind_check"),
            models.CheckConstraint(
                condition=models.Q(channel__in=["site", "telegram"]), name="club_faq_events_channel_check"
            ),
        ]
        indexes = [
            models.Index(fields=["-created_at"], name="club_faq_events_created_idx"),
            models.Index(fields=["kind", "gap_id"], name="club_faq_events_gap_idx"),
        ]


class MailOutbox(models.Model):
    id = models.BigAutoField(primary_key=True)
    kind = models.TextField(db_default="office")
    to_addr = models.CharField(max_length=255)
    owner_user_id = models.UUIDField(null=True)
    subject = models.TextField()
    body = models.TextField()
    status = models.CharField(max_length=255, db_default="pending")
    attempts = models.IntegerField(db_default=0)
    next_attempt_at = models.DateTimeField(db_default=Now())
    last_error = models.TextField(null=True)
    created_at = models.DateTimeField(db_default=Now())
    sent_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "club_mail_outbox"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["pending", "sent", "failed"]), name="club_mail_outbox_status_check"
            ),
        ]
        indexes = [
            models.Index(fields=["status", "next_attempt_at"], name="club_mail_outbox_due_idx"),
            models.Index(fields=["to_addr", "-created_at"], name="club_mail_outbox_confirmation_"),
        ]


class CaptchaUses(models.Model):
    salt_hash = models.CharField(max_length=64, primary_key=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "club_captcha_uses"
        indexes = [models.Index(fields=["expires_at"], name="club_captcha_uses_expires_idx")]


class NewsInbox(models.Model):
    id = models.CharField(max_length=255, primary_key=True)
    source_url = models.TextField(unique=True)
    sources = models.JSONField()
    title = models.TextField()
    published_at = models.DateTimeField(null=True)
    discovered_at = models.DateTimeField(db_default=Now())
    state = models.TextField(db_default="new")
    news = models.ForeignKey("News", on_delete=models.SET_NULL, db_column="news_id", related_name="+", null=True)

    class Meta:
        db_table = "club_news_inbox"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=["new", "imported", "dismissed"]), name="club_news_inbox_state_check"
            ),
        ]


class NewsSourceRuns(models.Model):
    source = models.CharField(max_length=255, primary_key=True)
    checked_at = models.DateTimeField(db_default=Now())
    found = models.IntegerField(db_default=0)
    error = models.TextField(null=True)

    class Meta:
        db_table = "club_news_source_runs"


class PageViews(models.Model):
    pk = models.CompositePrimaryKey("day", "path")
    day = models.DateField()
    path = models.CharField(max_length=512)
    hits = models.IntegerField(db_default=0)

    class Meta:
        db_table = "club_page_views"


class Settings(models.Model):
    key = models.CharField(max_length=255, primary_key=True)
    value = models.JSONField()
    updated_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "club_settings"


class SocialMembership(models.Model):
    alumni = models.OneToOneField(
        "Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", primary_key=True
    )
    telegram_id = models.TextField()
    subscribed = models.BooleanField()
    checked_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "club_social_membership"


class SocialReactions(models.Model):
    pk = models.CompositePrimaryKey("alumni_id", "chat_id", "message_id")
    alumni = models.ForeignKey(
        "Alumnus",
        on_delete=models.CASCADE,
        db_column="alumni_id",
        related_name="+",
    )
    chat_id = models.CharField(max_length=255)
    message_id = models.BigIntegerField()
    active = models.BooleanField()
    event_at = models.BigIntegerField()
    update_id = models.BigIntegerField(db_default=0)

    class Meta:
        db_table = "club_social_reactions"


class StaffSessions(models.Model):
    user = models.OneToOneField(
        "User", on_delete=models.CASCADE, db_column="user_id", related_name="+", primary_key=True
    )
    token_version = models.IntegerField(db_default=0)

    class Meta:
        db_table = "club_staff_sessions"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(token_version__gte=0), name="club_staff_sessions_token_version_check"
            ),
        ]


class SupportTickets(models.Model):
    id = models.UUIDField(primary_key=True)
    key_hash = models.TextField()
    request_hash = models.TextField()
    topic = models.TextField()
    messages = models.JSONField(default=list, db_default=models.Value([], output_field=models.JSONField()))
    status = models.TextField(db_default="open")
    consent_version = models.TextField()
    consent_text = models.TextField()
    consent_at = models.DateTimeField(db_default=Now())
    created_at = models.DateTimeField(db_default=Now())
    updated_at = models.DateTimeField(db_default=Now())
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "club_support_tickets"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(status__in=["open", "answered", "closed"]), name="club_support_tickets_status_check"
            ),
        ]
        indexes = [
            models.Index(fields=["expires_at"], name="club_support_expiry"),
        ]


class TelegramLinks(models.Model):
    alumni = models.OneToOneField(
        "Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", primary_key=True
    )
    token_hash = models.TextField(unique=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "club_telegram_links"
        indexes = [
            models.Index(fields=["expires_at"], name="club_telegram_links_expiry_idx"),
        ]


class MediaFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    storage = models.CharField(max_length=255)
    filename_disk = models.CharField(max_length=255, null=True)
    filename_download = models.CharField(max_length=255)
    title = models.CharField(max_length=255, null=True)
    type = models.CharField(max_length=255, null=True)
    folder = models.UUIDField(null=True)
    uploaded_by = models.ForeignKey(
        "User", on_delete=models.PROTECT, db_column="uploaded_by", related_name="+", null=True
    )
    created_on = models.DateTimeField(db_default=Now())
    modified_by = models.ForeignKey(
        "User", on_delete=models.PROTECT, db_column="modified_by", related_name="+", null=True
    )
    modified_on = models.DateTimeField(db_default=Now())
    charset = models.CharField(max_length=50, null=True)
    filesize = models.BigIntegerField(null=True)
    width = models.IntegerField(null=True)
    height = models.IntegerField(null=True)
    duration = models.IntegerField(null=True)
    embed = models.CharField(max_length=200, null=True)
    description = models.TextField(null=True)
    location = models.TextField(null=True)
    tags = models.TextField(null=True)
    metadata = models.JSONField(null=True)
    focal_point_x = models.IntegerField(null=True)
    focal_point_y = models.IntegerField(null=True)
    tus_id = models.CharField(max_length=64, null=True)
    tus_data = models.JSONField(null=True)
    uploaded_on = models.DateTimeField(null=True)

    class Meta:
        db_table = "directus_files"


class UserRole(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=64, db_default="supervised_user_circle")
    description = models.TextField(null=True)
    parent = models.ForeignKey("UserRole", on_delete=models.PROTECT, db_column="parent", related_name="+", null=True)

    class Meta:
        db_table = "directus_roles"


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    first_name = models.CharField(max_length=50, null=True)
    last_name = models.CharField(max_length=50, null=True)
    email = models.CharField(max_length=128, unique=True, null=True)
    password = models.CharField(max_length=255, null=True)
    location = models.CharField(max_length=255, null=True)
    title = models.CharField(max_length=50, null=True)
    description = models.TextField(null=True)
    tags = models.JSONField(null=True)
    avatar = models.UUIDField(null=True)
    language = models.CharField(max_length=255, null=True)
    tfa_secret = models.CharField(max_length=255, null=True)
    status = models.CharField(max_length=16, db_default="active")
    role = models.ForeignKey("UserRole", on_delete=models.SET_NULL, db_column="role", related_name="+", null=True)
    token = models.CharField(max_length=255, unique=True, null=True)
    last_access = models.DateTimeField(null=True)
    last_page = models.CharField(max_length=255, null=True)
    provider = models.CharField(max_length=128, db_default="default")
    external_identifier = models.CharField(max_length=255, unique=True, null=True)
    auth_data = models.JSONField(null=True)
    email_notifications = models.BooleanField(null=True, db_default=True)
    appearance = models.CharField(max_length=255, null=True)
    theme_dark = models.CharField(max_length=255, null=True)
    theme_light = models.CharField(max_length=255, null=True)
    theme_light_overrides = models.JSONField(null=True)
    theme_dark_overrides = models.JSONField(null=True)
    text_direction = models.CharField(max_length=255, db_default="auto")

    class Meta:
        db_table = "directus_users"


class EventRsvps(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    event = models.ForeignKey("Events", on_delete=models.CASCADE, db_column="event_id", related_name="+", null=True)
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    attended = models.BooleanField(null=True, db_default=False)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "event_rsvps"
        constraints = [
            models.UniqueConstraint(fields=("event", "alumni"), name="uq_event_rsvps_event_alumni"),
        ]


class Events(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    title = models.CharField(max_length=255, null=True)
    description = models.TextField(null=True)
    starts_at = models.DateTimeField(null=True)
    location = models.CharField(max_length=255, null=True)
    cover = models.CharField(max_length=255, null=True)
    reg_url = models.CharField(max_length=255, null=True)
    reminder_sent = models.BooleanField(null=True, db_default=False)
    format = models.CharField(max_length=255, null=True, db_default="offline")
    points = models.IntegerField(null=True, db_default=60)
    status = models.CharField(max_length=255, null=True, db_default="published")
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "events"


class Levels(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    key = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    min_points = models.IntegerField(null=True, db_default=0)
    discount_percent = models.IntegerField(null=True, db_default=0)
    sort = models.IntegerField(null=True)
    color = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "levels"


class News(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    slug = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    excerpt = models.TextField(null=True)
    body = models.TextField(null=True)
    source_url = models.CharField(max_length=255, null=True)
    published_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="draft")
    cover = models.CharField(max_length=500, null=True)
    origin = models.CharField(max_length=32, db_default="club")

    class Meta:
        db_table = "news"


class Offers(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    kind = models.CharField(max_length=255, null=True, db_default="level")
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    level_key = models.CharField(max_length=255, null=True)
    percent = models.IntegerField(null=True, db_default=0)
    title = models.CharField(max_length=255, null=True)
    active = models.BooleanField(null=True, db_default=True)
    valid_until = models.DateTimeField(null=True)

    class Meta:
        db_table = "offers"


class Orders(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    number = models.CharField(max_length=255, unique=True, null=True)
    alumni = models.ForeignKey("Alumnus", on_delete=models.SET_NULL, db_column="alumni_id", related_name="+", null=True)
    type = models.CharField(max_length=255, null=True, db_default="dpo")
    items_json = models.JSONField(null=True)
    subtotal = models.BigIntegerField(null=True, db_default=0)
    member_discount = models.IntegerField(null=True, db_default=0)
    total_estimate = models.BigIntegerField(null=True, db_default=0)
    contact_fio = models.CharField(max_length=255, null=True)
    contact_phone = models.CharField(max_length=255, null=True)
    contact_email = models.CharField(max_length=255, null=True)
    fulfillment = models.CharField(max_length=255, null=True, db_default="pickup")
    address = models.TextField(null=True)
    comment = models.TextField(null=True)
    consent_pdn = models.BooleanField(null=True, db_default=False)
    payment_id = models.CharField(max_length=255, null=True)
    payment_status = models.CharField(max_length=255, null=True)
    paid_at = models.DateTimeField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="new")
    created_at = models.DateTimeField(null=True, db_default=Now())
    program = models.ForeignKey(
        "Programs", on_delete=models.SET_NULL, db_column="program_id", related_name="+", null=True
    )
    participants = models.IntegerField(db_default=1)
    organization = models.CharField(max_length=300, null=True)
    consents = models.JSONField(null=True)
    promo = models.ForeignKey(
        "PromoCodes", on_delete=models.SET_NULL, db_column="promo_id", related_name="+", null=True
    )
    promo_code = models.CharField(max_length=32, null=True)
    discount_source = models.CharField(max_length=32, null=True)
    office_discount = models.IntegerField(null=True)
    office_reason = models.CharField(max_length=300, null=True)
    cohort = models.ForeignKey(
        "ProgramCohorts", on_delete=models.SET_NULL, db_column="cohort_id", related_name="+", null=True
    )

    class Meta:
        db_table = "orders"
        indexes = [
            models.Index(fields=["status"], name="idx_orders_status"),
            models.Index(fields=["created_at"], name="idx_orders_created"),
        ]


class Pages(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    slug = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    status = models.CharField(max_length=255, null=True, db_default="draft")
    sort = models.IntegerField(null=True)

    class Meta:
        db_table = "pages"


class PagesBlocks(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    collection = models.CharField(max_length=255, null=True)
    item = models.CharField(max_length=255, null=True)
    sort = models.IntegerField(null=True)
    pages = models.ForeignKey("Pages", on_delete=models.CASCADE, db_column="pages_id", related_name="+", null=True)

    class Meta:
        db_table = "pages_blocks"


class PodcastPlays(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    podcast = models.ForeignKey(
        "Podcasts", on_delete=models.CASCADE, db_column="podcast_id", related_name="+", null=True
    )
    alumni = models.ForeignKey("Alumnus", on_delete=models.SET_NULL, db_column="alumni_id", related_name="+", null=True)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "podcast_plays"


class Podcasts(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    title = models.CharField(max_length=255, null=True)
    description = models.TextField(null=True)
    cover = models.CharField(max_length=255, null=True)
    audio_url = models.CharField(max_length=255, null=True)
    video_url = models.CharField(max_length=255, null=True)
    duration = models.CharField(max_length=255, null=True)
    is_free = models.BooleanField(null=True, db_default=False)
    sort = models.IntegerField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="draft")
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "podcasts"


class PointRules(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    reason = models.CharField(max_length=255, unique=True, null=True)
    points = models.IntegerField(null=True, db_default=0)
    active = models.BooleanField(null=True, db_default=True)
    description = models.CharField(max_length=255, null=True)

    class Meta:
        db_table = "point_rules"


class PointsLedger(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    delta = models.IntegerField(null=True, db_default=0)
    reason = models.CharField(max_length=255, null=True)
    ref = models.CharField(max_length=255, null=True)
    comment = models.TextField(null=True)
    idempotency_key = models.CharField(max_length=255, unique=True, null=True)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "points_ledger"


class Products(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    slug = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    category = models.CharField(max_length=255, null=True)
    price = models.BigIntegerField(null=True, db_default=0)
    images = models.JSONField(null=True)
    variants_json = models.JSONField(null=True)
    stock = models.IntegerField(null=True, db_default=0)
    description = models.TextField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="draft")

    class Meta:
        db_table = "products"


class ProgramCohorts(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    program = models.ForeignKey("Programs", on_delete=models.CASCADE, db_column="program_id", related_name="+")
    starts_on = models.DateField()
    price = models.BigIntegerField(null=True)
    capacity = models.IntegerField(null=True)
    enrollment = models.CharField(max_length=32, db_default="actual")
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "program_cohorts"


class ProgramTrainers(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    program = models.ForeignKey("Programs", on_delete=models.CASCADE, db_column="program_id", related_name="+")
    trainer = models.ForeignKey("Trainers", on_delete=models.CASCADE, db_column="trainer_id", related_name="+")
    sort = models.IntegerField(db_default=0)

    class Meta:
        db_table = "program_trainers"
        constraints = [
            models.UniqueConstraint(fields=("program", "trainer"), name="uq_program_trainers_program_trainer"),
        ]


class Programs(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    slug = models.CharField(max_length=255, unique=True, null=True)
    title = models.CharField(max_length=255, null=True)
    direction = models.CharField(max_length=255, null=True)
    format = models.CharField(max_length=255, null=True, db_default="online")
    duration = models.CharField(max_length=255, null=True)
    price = models.BigIntegerField(null=True, db_default=0)
    dates = models.JSONField(null=True)
    capacity = models.IntegerField(null=True)
    seats_taken = models.IntegerField(null=True, db_default=0)
    modules = models.JSONField(null=True)
    teachers = models.JSONField(null=True)
    document = models.CharField(max_length=255, null=True)
    source_url = models.CharField(max_length=255, null=True)
    enrollment = models.CharField(max_length=255, null=True, db_default="actual")
    description = models.TextField(null=True)
    cover = models.CharField(max_length=255, null=True)
    hse_id = models.CharField(max_length=255, null=True)
    tagline = models.TextField(null=True)
    audience = models.JSONField(null=True)
    results = models.JSONField(null=True)
    advantages = models.JSONField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="draft")
    catalog = models.CharField(max_length=32, db_default=PROGRAM_CATALOGS[0])
    competencies = models.JSONField(null=True)
    communication = models.JSONField(null=True)

    class Meta:
        db_table = "programs"
        constraints = [
            models.CheckConstraint(condition=models.Q(catalog__in=PROGRAM_CATALOGS), name="programs_catalog_check"),
        ]


class PromoCodePrograms(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    promo = models.ForeignKey("PromoCodes", on_delete=models.CASCADE, db_column="promo_id", related_name="+")
    program = models.ForeignKey("Programs", on_delete=models.CASCADE, db_column="program_id", related_name="+")

    class Meta:
        db_table = "promo_code_programs"
        constraints = [
            models.UniqueConstraint(fields=("promo", "program"), name="uq_promo_code_programs_promo_program"),
        ]


class PromoCodes(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    code = models.CharField(max_length=32, unique=True)
    percent = models.IntegerField()
    starts_on = models.DateField()
    ends_on = models.DateField()
    total_limit = models.IntegerField(null=True)
    per_person_limit = models.IntegerField(null=True)
    all_programs = models.BooleanField(db_default=True)
    status = models.CharField(max_length=32, db_default="active")
    note = models.TextField(null=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "promo_codes"


class PushSubs(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    alumni = models.ForeignKey("Alumnus", on_delete=models.CASCADE, db_column="alumni_id", related_name="+", null=True)
    endpoint = models.TextField(null=True)
    keys = models.JSONField(null=True)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "push_subs"
        indexes = [
            models.Index(fields=["endpoint"], name="idx_push_subs_endpoint"),
        ]


class Referrals(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    referrer = models.ForeignKey(
        "Alumnus", on_delete=models.CASCADE, db_column="referrer_id", related_name="+", null=True
    )
    invited_user = models.ForeignKey(
        "User", on_delete=models.SET_NULL, db_column="invited_user_id", related_name="+", null=True
    )
    code = models.CharField(max_length=255, null=True)
    status = models.CharField(max_length=255, null=True, db_default="pending")
    reward_points = models.IntegerField(null=True, db_default=0)
    created_at = models.DateTimeField(null=True, db_default=Now())

    class Meta:
        db_table = "referrals"


class TimelineItems(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    year = models.CharField(max_length=255, null=True)
    title = models.CharField(max_length=255, null=True)
    text = models.TextField(null=True)
    metric = models.CharField(max_length=255, null=True)
    sort = models.IntegerField(null=True)
    status = models.CharField(max_length=255, null=True, db_default="published")

    class Meta:
        db_table = "timeline_items"


class Trainers(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, db_default=DatabaseUUID())
    fio = models.CharField(max_length=255)
    photo = models.CharField(max_length=255, null=True)
    bio = models.TextField(null=True)
    status = models.CharField(max_length=255, db_default="draft")
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        db_table = "trainers"
