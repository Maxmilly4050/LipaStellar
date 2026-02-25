"""
Withdrawal views — Phase 2 treasury management.

App customers (phone-based) request cash-out via mobile money.
The flow:
  1. Customer submits withdrawal amount → withdrawal_request
  2. Request is stored with status='requested' (balance NOT yet debited)
  3. Staff admin approves/rejects via withdrawal_approve
  4. On approval, mobile money payout is initiated and balance debited
"""
import logging
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import Customer, Withdrawal, TreasuryLog
from .mobile_money import get_mobile_money_provider
from . import stellar_utils
from .views_deposit import _get_session_customer

logger = logging.getLogger(__name__)

MIN_WITHDRAWAL = Decimal('500')
TZS_PER_USDC = Decimal('2500')


def withdrawal_request(request):
    """
    Customer requests a withdrawal (cash-out) to their mobile wallet.
    Balance is NOT debited here — only after staff approval.
    """
    customer = _get_session_customer(request)
    if not customer:
        messages.info(request, "Please log in with your phone number first.")
        return redirect('customer_login')

    if request.method == 'POST':
        raw_amount = request.POST.get('amount_tzs', '').strip()
        payout_method = request.POST.get('payout_method', 'mobile_money')

        try:
            amount_tzs = Decimal(raw_amount)
        except Exception:
            messages.error(request, "Invalid amount.")
            return render(request, 'payments/withdrawal_request.html', {'customer': customer})

        if amount_tzs < MIN_WITHDRAWAL:
            messages.error(request, f"Minimum withdrawal is {MIN_WITHDRAWAL:,.0f} TZS.")
            return render(request, 'payments/withdrawal_request.html', {'customer': customer})

        amount_usdc = (amount_tzs / TZS_PER_USDC).quantize(Decimal('0.0000001'))

        if customer.balance_usdc < amount_usdc:
            messages.error(
                request,
                f"Insufficient balance. You have {customer.balance_usdc:.2f} USDC "
                f"({customer.balance_usdc * TZS_PER_USDC:,.0f} TZS), "
                f"need {amount_usdc:.2f} USDC ({amount_tzs:,.0f} TZS)."
            )
            return render(request, 'payments/withdrawal_request.html', {'customer': customer})

        Withdrawal.objects.create(
            customer=customer,
            amount_tzs=amount_tzs,
            amount_usdc=amount_usdc,
            payout_method=payout_method,
            status='requested',
        )

        messages.success(
            request,
            "Withdrawal request submitted! It will be processed within 24 hours."
        )
        return redirect('withdrawal_history')

    return render(request, 'payments/withdrawal_request.html', {
        'customer': customer,
        'min_withdrawal': MIN_WITHDRAWAL,
    })


def withdrawal_history(request):
    """Customer's withdrawal history."""
    customer = _get_session_customer(request)
    if not customer:
        return redirect('customer_login')
    withdrawals = Withdrawal.objects.filter(customer=customer).order_by('-requested_at')[:20]
    return render(request, 'payments/withdrawal_history.html', {
        'customer': customer,
        'withdrawals': withdrawals,
    })


@staff_member_required
def withdrawal_pending_list(request):
    """Staff view: list all pending withdrawal requests."""
    withdrawals = Withdrawal.objects.filter(status='requested').select_related('customer').order_by('requested_at')
    return render(request, 'payments/withdrawal_pending.html', {'withdrawals': withdrawals})


@staff_member_required
def withdrawal_approve(request, withdrawal_id):
    """
    Staff approves or rejects a withdrawal request.
    On approval: debits customer balance, initiates mobile money payout.
    """
    withdrawal = get_object_or_404(Withdrawal, id=withdrawal_id, status='requested')

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'approve':
            from django.db import transaction as db_transaction

            with db_transaction.atomic():
                customer = Customer.objects.select_for_update().get(pk=withdrawal.customer.pk)

                if customer.balance_usdc < withdrawal.amount_usdc:
                    messages.error(request, "Customer has insufficient balance for this withdrawal.")
                    return redirect('withdrawal_pending_list')

                provider = get_mobile_money_provider()
                payout = provider.send_payout(
                    phone_number=customer.phone_number or '',
                    amount_tzs=withdrawal.amount_tzs,
                    reference=f"WIT{withdrawal.pk}",
                )

                if not payout['success']:
                    messages.error(request, f"Payout failed: {payout.get('error', 'Unknown error')}")
                    return redirect('withdrawal_pending_list')

                # Debit balance
                customer.balance_usdc -= withdrawal.amount_usdc
                customer.save(update_fields=['balance_usdc', 'last_seen'])

                withdrawal.status = 'processing'
                withdrawal.processed_at = timezone.now()
                withdrawal.approved_by = request.user
                withdrawal.approved_at = timezone.now()
                withdrawal.provider_reference = payout['reference']
                withdrawal.save()

                try:
                    master_bal = stellar_utils.get_master_balance()
                except Exception:
                    master_bal = Decimal('0')

                TreasuryLog.objects.create(
                    event_type='withdrawal',
                    master_balance_before=master_bal,
                    master_balance_after=master_bal,
                    withdrawal=withdrawal,
                    created_by=request.user,
                    notes=f"Withdrawal approved for {customer.phone_number}: -{withdrawal.amount_usdc} USDC",
                )

                messages.success(request, f"Withdrawal approved and payout initiated (ref: {payout['reference']}).")

        elif action == 'reject':
            reason = request.POST.get('reason', 'No reason given.')
            withdrawal.status = 'cancelled'
            withdrawal.notes = reason
            withdrawal.save(update_fields=['status', 'notes'])
            messages.info(request, f"Withdrawal #{withdrawal_id} rejected.")

        return redirect('withdrawal_pending_list')

    return render(request, 'payments/withdrawal_approve.html', {'withdrawal': withdrawal})


@staff_member_required
def treasury_dashboard(request):
    """Staff treasury overview: master balance, all customer balances, recent activity."""
    from django.db.models import Sum
    from datetime import timedelta

    try:
        master_balance = stellar_utils.get_master_balance()
    except Exception as exc:
        master_balance = Decimal('0')
        messages.warning(request, f"Could not fetch master balance: {exc}")

    db_total = Customer.objects.filter(
        customer_type='app'
    ).aggregate(total=Sum('balance_usdc'))['total'] or Decimal('0')

    discrepancy = master_balance - db_total

    now = timezone.now()
    day_ago = now - timedelta(days=1)

    recent_deposits = (
        __import__('payments.models', fromlist=['Deposit']).Deposit
        .objects.filter(created_at__gte=day_ago).select_related('customer').order_by('-created_at')[:10]
    )
    recent_withdrawals = (
        __import__('payments.models', fromlist=['Withdrawal']).Withdrawal
        .objects.filter(requested_at__gte=day_ago).select_related('customer').order_by('-requested_at')[:10]
    )

    from .models import Deposit, Withdrawal, TreasuryLog  # noqa: avoid circular

    recent_deposits = Deposit.objects.filter(created_at__gte=day_ago).select_related('customer').order_by('-created_at')[:10]
    recent_withdrawals = Withdrawal.objects.filter(requested_at__gte=day_ago).select_related('customer').order_by('-requested_at')[:10]
    pending_withdrawals_count = Withdrawal.objects.filter(status='requested').count()
    reconciliation_log = TreasuryLog.objects.order_by('-created_at')[:20]

    return render(request, 'payments/treasury_dashboard.html', {
        'master_balance': master_balance,
        'db_total': db_total,
        'discrepancy': discrepancy,
        'recent_deposits': recent_deposits,
        'recent_withdrawals': recent_withdrawals,
        'pending_withdrawals_count': pending_withdrawals_count,
        'reconciliation_log': reconciliation_log,
    })
