"""
Deposit views — Phase 2 treasury management.

App customers (Path B, phone-based) fund their USDC balance via mobile money.
The flow:
  1. Customer enters TZS amount → deposit_request
  2. MockProvider (or real provider) initiates the charge
  3. On success, customer balance is credited and a TreasuryLog entry written
  4. deposit_status provides a polling endpoint for the pending-state page
"""
import logging
from decimal import Decimal

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import Customer, Deposit, TreasuryLog
from .mobile_money import get_mobile_money_provider
from . import stellar_utils

logger = logging.getLogger(__name__)

MIN_DEPOSIT = Decimal('500')    # TZS
MAX_DEPOSIT = Decimal('500000') # TZS
TZS_PER_USDC = Decimal('2500')


def _get_session_customer(request):
    """
    Return the Customer object for the current session, or None.
    Customers authenticate via phone number stored in session['customer_phone'].
    """
    phone = request.session.get('customer_phone')
    if not phone:
        return None
    return Customer.objects.filter(phone_number=phone, customer_type='app', is_active=True).first()


def customer_login(request):
    """
    Simple phone-number based customer login (no password on testnet).
    Stores phone in session; creates app customer record if needed.
    """
    # Block if a merchant is already logged in via Django auth
    if request.user.is_authenticated:
        messages.warning(
            request,
            "You are currently logged in as a merchant. "
            "Please log out of your merchant account before logging in as a customer."
        )
        return redirect('dashboard')

    if request.method == 'POST':
        phone = request.POST.get('phone', '').strip()
        if not phone.startswith('+'):
            messages.error(request, "Enter your phone in international format (e.g. +255712345678).")
            return render(request, 'payments/customer_login.html')

        customer, created = Customer.objects.get_or_create(
            phone_number=phone,
            customer_type='app',
            defaults={
                'stellar_memo': Customer.generate_memo(),
                'balance_usdc': Decimal('0'),
                'is_active': True,
            }
        )
        request.session['customer_phone'] = phone
        request.session.modified = True
        if created:
            messages.success(request, f"Welcome! Your account has been created. Your payment memo is: {customer.stellar_memo}")
        else:
            messages.success(request, f"Welcome back! Balance: {customer.balance_usdc:.2f} USDC")
        return redirect('customer_dashboard')
    return render(request, 'payments/customer_login.html')


def customer_logout(request):
    request.session.pop('customer_phone', None)
    request.session.modified = True
    return redirect('index')


def deposit_request(request):
    """
    Customer initiates a USDC deposit by paying via mobile money.
    Uses the session-based customer auth (customer_phone in session).
    """
    customer = _get_session_customer(request)
    if not customer:
        messages.info(request, "Please log in with your phone number first.")
        return redirect('customer_login')

    if request.method == 'POST':
        raw_amount = request.POST.get('amount_tzs', '').strip()
        try:
            amount_tzs = Decimal(raw_amount)
        except Exception:
            messages.error(request, "Invalid amount.")
            return render(request, 'payments/deposit_request.html', {
                'customer': customer,
                'min_deposit': MIN_DEPOSIT,
                'max_deposit': MAX_DEPOSIT,
            })

        if amount_tzs < MIN_DEPOSIT:
            messages.error(request, f"Minimum deposit is {MIN_DEPOSIT:,.0f} TZS.")
            return render(request, 'payments/deposit_request.html', {
                'customer': customer, 'min_deposit': MIN_DEPOSIT, 'max_deposit': MAX_DEPOSIT,
            })
        if amount_tzs > MAX_DEPOSIT:
            messages.error(request, f"Maximum deposit is {MAX_DEPOSIT:,.0f} TZS.")
            return render(request, 'payments/deposit_request.html', {
                'customer': customer, 'min_deposit': MIN_DEPOSIT, 'max_deposit': MAX_DEPOSIT,
            })

        amount_usdc = (amount_tzs / TZS_PER_USDC).quantize(Decimal('0.0000001'))

        deposit = Deposit.objects.create(
            customer=customer,
            amount_tzs=amount_tzs,
            amount_usdc=amount_usdc,
            payment_method='mobile_money',
            status='pending',
        )

        provider = get_mobile_money_provider()
        result = provider.request_payment(
            phone_number=customer.phone_number or '',
            amount_tzs=amount_tzs,
            reference=f"DEP{deposit.pk}",
        )

        if result['success']:
            deposit.provider_reference = result['reference']
            deposit.status = 'processing'
            deposit.save(update_fields=['provider_reference', 'status'])

            # For MockProvider: complete immediately
            from django.conf import settings
            if settings.DEBUG:
                _complete_deposit(deposit)
                messages.success(
                    request,
                    f"Deposit of {amount_tzs:,.0f} TZS ({amount_usdc:.2f} USDC) completed! "
                    f"New balance: {customer.balance_usdc:.2f} USDC"
                )
                return redirect('deposit_history')

            return redirect('deposit_pending', deposit_id=deposit.pk)
        else:
            deposit.status = 'failed'
            deposit.notes = result.get('error', 'Provider error')
            deposit.save(update_fields=['status', 'notes'])
            messages.error(request, f"Payment request failed: {result.get('error', 'Unknown error')}")
            return render(request, 'payments/deposit_request.html', {
                'customer': customer, 'min_deposit': MIN_DEPOSIT, 'max_deposit': MAX_DEPOSIT,
            })

    return render(request, 'payments/deposit_request.html', {
        'customer': customer,
        'min_deposit': MIN_DEPOSIT,
        'max_deposit': MAX_DEPOSIT,
    })


def _complete_deposit(deposit: Deposit):
    """Credit customer balance and write TreasuryLog (called on confirmed deposit)."""
    from django.db import transaction as db_transaction

    with db_transaction.atomic():
        # Fetch live master balance for logging
        try:
            master_before = stellar_utils.get_master_balance()
        except Exception:
            master_before = Decimal('0')

        customer = Customer.objects.select_for_update().get(pk=deposit.customer.pk)
        customer.balance_usdc += deposit.amount_usdc
        customer.save(update_fields=['balance_usdc', 'last_seen'])

        deposit.status = 'completed'
        deposit.completed_at = timezone.now()
        deposit.save(update_fields=['status', 'completed_at'])

        TreasuryLog.objects.create(
            event_type='deposit',
            master_balance_before=master_before,
            master_balance_after=master_before,  # No on-chain movement yet
            deposit=deposit,
            notes=f"Deposit completed for {customer.phone_number}: +{deposit.amount_usdc} USDC",
        )


def deposit_webhook(request):
    """
    Webhook endpoint called by the mobile money provider when a deposit is confirmed.
    """
    provider = get_mobile_money_provider()
    result = provider.handle_webhook(request)

    if not result['success']:
        logger.warning("Webhook rejected: %s", result.get('error'))
        return JsonResponse({'status': 'ignored'})

    try:
        deposit = Deposit.objects.get(
            provider_reference=result['reference'],
            status__in=['pending', 'processing'],
        )
        _complete_deposit(deposit)
        return JsonResponse({'status': 'ok'})
    except Deposit.DoesNotExist:
        logger.error("Webhook: no matching deposit for reference %s", result.get('reference'))
        return JsonResponse({'status': 'not_found'}, status=404)
    except Exception as exc:
        logger.error("Webhook processing error: %s", exc)
        return JsonResponse({'status': 'error', 'detail': str(exc)}, status=500)


@require_GET
def deposit_status(request, deposit_id):
    """Polling endpoint — returns current deposit status as JSON."""
    deposit = get_object_or_404(Deposit, id=deposit_id)
    return JsonResponse({
        'status': deposit.status,
        'amount_tzs': float(deposit.amount_tzs),
        'amount_usdc': float(deposit.amount_usdc),
    })


def deposit_pending(request, deposit_id):
    """Pending page shown while waiting for mobile money confirmation."""
    deposit = get_object_or_404(Deposit, id=deposit_id)
    return render(request, 'payments/deposit_pending.html', {'deposit': deposit})


def customer_dashboard(request):
    """App customer home — balance, memo, recent deposits & withdrawals."""
    from django.db.models import Sum
    from .models import Withdrawal
    customer = _get_session_customer(request)
    if not customer:
        messages.info(request, "Please log in with your phone number first.")
        return redirect('customer_login')

    recent_deposits = Deposit.objects.filter(customer=customer).order_by('-created_at')[:5]
    recent_withdrawals = Withdrawal.objects.filter(customer=customer).order_by('-requested_at')[:5]

    total_deposited = Deposit.objects.filter(
        customer=customer, status='completed'
    ).aggregate(total=Sum('amount_usdc'))['total'] or Decimal('0')

    total_withdrawn = Withdrawal.objects.filter(
        customer=customer, status__in=['processing', 'completed']
    ).aggregate(total=Sum('amount_usdc'))['total'] or Decimal('0')

    return render(request, 'payments/customer_dashboard.html', {
        'customer': customer,
        'recent_deposits': recent_deposits,
        'recent_withdrawals': recent_withdrawals,
        'total_deposited': total_deposited,
        'total_withdrawn': total_withdrawn,
        'tzs_per_usdc': TZS_PER_USDC,
    })


def deposit_history(request):
    """Customer's deposit history."""
    customer = _get_session_customer(request)
    if not customer:
        return redirect('customer_login')
    deposits = Deposit.objects.filter(customer=customer).order_by('-created_at')[:20]
    return render(request, 'payments/deposit_history.html', {
        'customer': customer,
        'deposits': deposits,
    })
