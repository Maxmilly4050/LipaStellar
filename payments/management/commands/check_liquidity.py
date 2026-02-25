"""
Check master account USDC liquidity and issue alerts if below thresholds.

Usage:
  python manage.py check_liquidity
  python manage.py check_liquidity --critical-threshold 25 --low-threshold 75

Can be run on a cron schedule (e.g. every 15 minutes).
"""
import os
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from payments.models import Customer, Transaction, TreasuryLog, LiquidityAlert
from payments import stellar_utils


class Command(BaseCommand):
    help = 'Check master account liquidity and alert if below thresholds.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--low-threshold',
            type=float,
            default=float(os.getenv('LIQUIDITY_LOW_THRESHOLD', '100')),
            help='USDC balance that triggers a low-balance warning.',
        )
        parser.add_argument(
            '--critical-threshold',
            type=float,
            default=float(os.getenv('LIQUIDITY_CRITICAL_THRESHOLD', '50')),
            help='USDC balance that triggers a critical alert.',
        )

    def handle(self, *args, **options):
        low_threshold = Decimal(str(options['low_threshold']))
        critical_threshold = Decimal(str(options['critical_threshold']))

        try:
            master_balance = stellar_utils.get_master_balance()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Cannot fetch master balance: {exc}"))
            return

        # 24-hour payment volume
        since = timezone.now() - timedelta(hours=24)
        recent_volume = (
            Transaction.objects.filter(
                created_at__gte=since,
                status='completed',
                direction='inbound',
                amount_usdc__isnull=False,
            ).aggregate(total=Sum('amount_usdc'))['total'] or Decimal('0')
        )

        # Projected runway
        if recent_volume > Decimal('0'):
            hours_remaining = float(master_balance / recent_volume) * 24
        else:
            hours_remaining = float('inf')

        self.stdout.write(
            f"Master balance : {master_balance:.4f} USDC  |  "
            f"24 h volume : {recent_volume:.4f} USDC  |  "
            f"Est. runway : {'∞' if hours_remaining == float('inf') else f'{hours_remaining:.1f} h'}"
        )

        if master_balance < critical_threshold:
            self.stdout.write(self.style.ERROR(
                f"\n  🚨 CRITICAL: Balance {master_balance:.2f} USDC is below "
                f"critical threshold ({critical_threshold} USDC)!"
            ))
            LiquidityAlert.objects.create(
                threshold=critical_threshold,
                current_balance=master_balance,
            )
            TreasuryLog.objects.create(
                event_type='alert',
                master_balance_before=master_balance,
                master_balance_after=master_balance,
                notes=(
                    f"CRITICAL liquidity alert: balance {master_balance:.4f} USDC "
                    f"< threshold {critical_threshold} USDC"
                ),
            )
            # Stub: send SMS / email / Slack here
        elif master_balance < low_threshold:
            self.stdout.write(self.style.WARNING(
                f"\n  ⚠️  LOW: Balance {master_balance:.2f} USDC is below "
                f"low threshold ({low_threshold} USDC)."
            ))
            TreasuryLog.objects.create(
                event_type='alert',
                master_balance_before=master_balance,
                master_balance_after=master_balance,
                notes=(
                    f"Low liquidity warning: balance {master_balance:.4f} USDC "
                    f"< threshold {low_threshold} USDC"
                ),
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"\n  ✓ Liquidity OK ({master_balance:.4f} USDC)"))
