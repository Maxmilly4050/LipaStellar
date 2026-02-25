"""
Migrate existing phone-based customer records (if any) to the new
Customer model with customer_type='app', ensuring every record has a
unique stellar_memo.

Usage:
  python manage.py migrate_customers
  python manage.py migrate_customers --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction as db_transaction

from payments.models import Customer


class Command(BaseCommand):
    help = 'Migrate existing phone-based customers to the new dual-path Customer model.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would happen without making changes.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Customer migration {'(DRY RUN) ' if dry_run else ''}started")
        self.stdout.write("=" * 60)

        # Find app customers missing a memo
        missing_memo = Customer.objects.filter(customer_type='app', stellar_memo__isnull=True)
        self.stdout.write(f"  App customers missing memo  : {missing_memo.count()}")

        fixed = 0
        for customer in missing_memo:
            memo = Customer.generate_memo()
            self.stdout.write(
                f"  {'[DRY] ' if dry_run else ''}Phone {customer.phone_number} → memo {memo}"
            )
            if not dry_run:
                customer.stellar_memo = memo
                customer.save(update_fields=['stellar_memo'])
                fixed += 1

        # Find wallet customers missing public key (data integrity)
        bad_wallet = Customer.objects.filter(customer_type='wallet', stellar_public_key__isnull=True)
        if bad_wallet.exists():
            self.stdout.write(
                self.style.WARNING(
                    f"  ⚠️  {bad_wallet.count()} wallet customer(s) missing stellar_public_key — "
                    "manual investigation needed."
                )
            )

        self.stdout.write("=" * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f"  {'Would fix' if dry_run else 'Fixed'} {fixed} customer(s)."
            )
        )
        self.stdout.write("=" * 60)
