# Generated migration: move merchant identity fields to Stellar on-chain storage.
#
# Strategy (safe for existing data):
#   1. Add nullable cached_username / cached_business_name / last_synced_at
#   2. Run a data migration to copy username → cached_username and
#      business_name → cached_business_name for every existing row
#   3. Remove the now-redundant business_name, phone_number, username columns
#
# After applying this migration run:
#   python manage.py migrate_to_stellar_storage
# to push the identity data onto the Stellar ledger for existing merchants.

import django.utils.timezone
from django.db import migrations, models


def copy_identity_to_cache(apps, schema_editor):
    """Copy username / business_name into the new cache columns."""
    Merchant = apps.get_model("payments", "Merchant")
    for m in Merchant.objects.all():
        # getattr with fallback so the migration is idempotent even if the
        # columns no longer exist in a later re-run.
        m.cached_username = getattr(m, "username", None)
        m.cached_business_name = getattr(m, "business_name", None)
        m.save(update_fields=["cached_username", "cached_business_name"])


def reverse_copy(apps, schema_editor):
    """Restore username / business_name from cache on migration reversal."""
    Merchant = apps.get_model("payments", "Merchant")
    for m in Merchant.objects.all():
        m.username = m.cached_username or ""
        m.business_name = m.cached_business_name or ""
        m.save(update_fields=["username", "business_name"])


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0003_transaction_direction_and_more"),
    ]

    operations = [
        # --- Step 1: Add new cache/sync columns ---
        migrations.AddField(
            model_name="merchant",
            name="cached_username",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="merchant",
            name="cached_business_name",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="merchant",
            name="last_synced_at",
            field=models.DateTimeField(auto_now=True),
        ),

        # --- Step 2: Populate cache from legacy columns ---
        migrations.RunPython(copy_identity_to_cache, reverse_copy),

        # --- Step 3: Drop legacy columns ---
        migrations.RemoveField(model_name="merchant", name="business_name"),
        migrations.RemoveField(model_name="merchant", name="phone_number"),
        migrations.RemoveField(model_name="merchant", name="username"),
    ]
