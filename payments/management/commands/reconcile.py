"""
Reconcile the sum of all app customer USDC balances against the master
Stellar account's live USDC balance.

Usage:
  python manage.py reconcile
  python manage.py reconcile --email        # (stub) send email alert
  python manage.py reconcile --fix          # placeholder for auto-fix
"""
import logging
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from payments.models import Customer, TreasuryLog
from payments import stellar_utils

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Reconcile database USDC balances with master Stellar account.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Attempt automatic fix (currently logs discrepancy only).',
        )
        parser.add_argument(
            '--email',
            action='store_true',
            help='Send email alert on discrepancy (stub).',
        )

    def handle(self, *args, **options):
        self.stdout.write("=" * 60)
        self.stdout.write(f"  Reconciliation at {timezone.now():%Y-%m-%d %H:%M:%S UTC}")
        self.stdout.write("=" * 60)

        # Master account balance
        try:
            master_balance = stellar_utils.get_master_balance()
            self.stdout.write(f"  Master USDC balance : {master_balance:.7f} USDC")
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"  Cannot fetch master balance: {exc}"))
            return

        # Sum of all app customer off-chain balances
        db_total = Customer.objects.filter(
            customer_type='app'
        ).aggregate(total=Sum('balance_usdc'))['total'] or Decimal('0')
        self.stdout.write(f"  DB customer total   : {db_total:.7f} USDC")

        discrepancy = master_balance - db_total
        self.stdout.write(f"  Discrepancy         : {discrepancy:.7f} USDC")

        # Log to DB
        TreasuryLog.objects.create(
            event_type='reconciliation',
            master_balance_before=master_balance,
            master_balance_after=master_balance,
            db_total_before=db_total,
            db_total_after=db_total,
            discrepancy=discrepancy,
            notes=f"Reconciliation run. Discrepancy: {discrepancy:.7f} USDC",
        )

        if discrepancy == Decimal('0'):
            self.stdout.write(self.style.SUCCESS("\n  ✓ Balanced — no discrepancy found."))
        else:
            self.stdout.write(self.style.ERROR(f"\n  ✗ DISCREPANCY of {discrepancy:.7f} USDC detected!"))
            if options['fix']:
                self.stdout.write(self.style.WARNING(
                    "  --fix requested but auto-fix is not implemented. "
                    "Manual investigation required."
                ))
            if options['email']:
                self.stdout.write(self.style.WARNING("  --email stub: email alert would be sent here."))

        self.stdout.write("=" * 60)
