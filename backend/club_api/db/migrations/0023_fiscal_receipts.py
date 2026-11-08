import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("club_data", "0022_donations_foreign_key_actions"),
    ]

    operations = [
        migrations.CreateModel(
            name="FiscalReceipts",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("kind", models.CharField(db_default="sale", max_length=16)),
                ("idempotency_key", models.CharField(max_length=36, unique=True)),
                ("request", models.JSONField()),
                ("status", models.CharField(db_default="pending", max_length=16)),
                ("document_id", models.CharField(max_length=20, null=True)),
                ("attempts", models.IntegerField(db_default=0)),
                (
                    "next_attempt_at",
                    models.DateTimeField(db_default=django.db.models.functions.datetime.Now()),
                ),
                ("last_error", models.TextField(null=True)),
                ("fiscal", models.JSONField(null=True)),
                (
                    "created_at",
                    models.DateTimeField(db_default=django.db.models.functions.datetime.Now()),
                ),
                (
                    "updated_at",
                    models.DateTimeField(db_default=django.db.models.functions.datetime.Now()),
                ),
                (
                    "order",
                    models.ForeignKey(
                        db_column="order_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="club_data.orders",
                    ),
                ),
            ],
            options={
                "db_table": "club_fiscal_receipts",
                "indexes": [
                    models.Index(
                        fields=["status", "next_attempt_at"],
                        name="club_fiscal_receipts_due_idx",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "status__in",
                                ["pending", "registered", "success", "failed"],
                            )
                        ),
                        name="club_fiscal_receipts_status_check",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("kind__in", ["sale"])),
                        name="club_fiscal_receipts_kind_check",
                    ),
                ],
            },
        ),
    ]
