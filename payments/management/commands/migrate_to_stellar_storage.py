"""
Management command: migrate_to_stellar_storage
==============================================

Pushes merchant identity data (username, business name, phone, created timestamp)
from the PostgreSQL cache onto the Stellar ledger as ManageData entries.

This command is safe to run multiple times — it skips accounts that already
have a 'username' ManageData entry (idempotent).

Usage
-----
    # Dry run first (no transactions submitted)
    python manage.py migrate_to_stellar_storage --dry-run

    # Migrate in batches of 5 with a 3-second delay between transactions
    python manage.py migrate_to_stellar_storage --batch-size 5 --delay 3

    # Migrate all merchants
    python manage.py migrate_to_stellar_storage

Notes
-----
  * If the Merchant model no longer has the legacy username/business_name/
    phone_number columns (because migration 0004 removed them), the command
    falls back to the cached_ fields.
  * The Stellar secret key is decrypted only inside this command for the
    duration of transaction signing and is never logged or stored in plaintext.
"""

import logging
import time

from django.core.management.base import BaseCommand, CommandError

from payments import stellar_utils
from payments.models import Merchant

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Migrate existing merchant profiles from PostgreSQL cache to Stellar ManageData"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of merchants to process per batch (default: 10).",
        )
        parser.add_argument(
            "--delay",
            type=float,
            default=2.0,
            help="Seconds to wait between transactions to avoid Horizon rate limits (default: 2.0).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the migration without submitting any transactions.",
        )
        parser.add_argument(
            "--merchant-id",
            type=int,
            default=None,
            help="Migrate a single merchant by database ID (for testing).",
        )

    def handle(self, *args, **options):  # noqa: C901
        batch_size = options["batch_size"]
        delay = options["delay"]
        dry_run = options["dry_run"]
        single_id = options["merchant_id"]

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN MODE — no transactions will be submitted.\n"))

        # Filter to a single merchant if requested
        qs = Merchant.objects.all()
        if single_id:
            qs = qs.filter(pk=single_id)
            if not qs.exists():
                raise CommandError(f"No merchant with id={single_id}")

        total = qs.count()
        self.stdout.write(f"Found {total} merchant(s) to process.\n")

        success_count = 0
        fail_count = 0
        skipped_count = 0

        for idx, merchant in enumerate(qs.iterator(), start=1):
            pk_label = f"id={merchant.pk} key={merchant.stellar_public_key[:8]}…"
            self.stdout.write(f"\n[{idx}/{total}] {pk_label}")

            # ── Check if already migrated ──────────────────────────────────
            try:
                existing = stellar_utils.get_merchant_profile(merchant.stellar_public_key)
                if existing and stellar_utils.DATA_KEY_USERNAME in existing:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  ⏭  Already has on-chain username='{existing[stellar_utils.DATA_KEY_USERNAME]}'. Skipping."
                        )
                    )
                    skipped_count += 1
                    continue
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"  ⚠  Could not check existing data: {exc}"))

            # ── Build profile_data from available fields ───────────────────
            # After migration 0004 the legacy columns are gone; we rely on the
            # cached_ fields which were populated by the data migration step.
            username = getattr(merchant, "cached_username", None) or getattr(merchant, "username", None)
            business = getattr(merchant, "cached_business_name", None) or getattr(merchant, "business_name", None)
            phone    = getattr(merchant, "phone_number", None)  # may be None post-migration

            if not username:
                self.stdout.write(
                    self.style.ERROR(
                        f"  ✗  No username found for merchant id={merchant.pk}. Skipping."
                    )
                )
                fail_count += 1
                continue

            profile_data = {
                stellar_utils.DATA_KEY_USERNAME: username,
                stellar_utils.DATA_KEY_CREATED:  str(int(merchant.created_at.timestamp())),
            }
            if business:
                profile_data[stellar_utils.DATA_KEY_BUSINESS] = str(business)[:60]
            if phone:
                profile_data[stellar_utils.DATA_KEY_PHONE] = str(phone)[:60]

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(f"  ✔  [DRY RUN] Would store: {profile_data}")
                )
                success_count += 1
                continue

            # ── Submit transaction ─────────────────────────────────────────
            try:
                secret = merchant.get_secret_key()  # audited decryption

                tx_hash = stellar_utils.store_merchant_profile(secret, profile_data)

                # Keep cache in sync
                update_fields = ["last_synced_at"]
                if "cached_username" not in [f.name for f in merchant._meta.get_fields()]:
                    pass  # field doesn't exist (shouldn't happen after migration)
                else:
                    merchant.cached_username = username
                    update_fields.append("cached_username")
                if business and hasattr(merchant, "cached_business_name"):
                    merchant.cached_business_name = business
                    update_fields.append("cached_business_name")
                merchant.save(update_fields=update_fields)

                self.stdout.write(
                    self.style.SUCCESS(f"  ✔  Migrated — tx={tx_hash[:16]}…")
                )
                success_count += 1

            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"  ✗  Failed: {exc}"))
                logger.error("migrate_to_stellar_storage failed for merchant id=%s: %s", merchant.pk, exc)
                fail_count += 1

            # ── Rate limiting ──────────────────────────────────────────────
            if idx % batch_size == 0 and idx < total:
                self.stdout.write(f"\n  ── Batch pause {delay}s after {idx}/{total} ──")
            time.sleep(delay)

            # Progress report every 10 merchants
            if idx % 10 == 0:
                self.stdout.write(f"\nProgress: {idx}/{total} processed so far.")

        # ── Summary ────────────────────────────────────────────────────────
        self.stdout.write("\n" + "=" * 56)
        self.stdout.write(self.style.SUCCESS("MIGRATION COMPLETE"))
        self.stdout.write(f"  Total:    {total}")
        self.stdout.write(self.style.SUCCESS(f"  Success:  {success_count}"))
        if fail_count:
            self.stdout.write(self.style.ERROR(f"  Failed:   {fail_count}"))
        if skipped_count:
            self.stdout.write(self.style.WARNING(f"  Skipped:  {skipped_count}"))
        self.stdout.write("=" * 56 + "\n")

        if fail_count:
            raise CommandError(
                f"{fail_count} merchant(s) failed to migrate. "
                "Check the output above and audit.log for details."
            )
