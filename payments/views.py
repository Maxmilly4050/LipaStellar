import datetime
import logging
import time

from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

logger = logging.getLogger(__name__)

from .models import Merchant, Transaction, Customer
from .forms import (
    MerchantRegistrationForm, CustomerPaymentForm,
    CustomerPaymentMethodForm, WalletPaymentForm, PhonePaymentForm,
)
from . import stellar_utils

# Fixed exchange rate for demo (1 XLM = 300 TZS)
TZS_PER_XLM = Decimal('300')

def index(request):
    """Landing page — only shown when nobody is logged in."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.session.get('customer_phone'):
        return redirect('customer_dashboard')
    return render(request, 'payments/index.html')


def merchant_login(request):
    """
    Custom merchant login view that blocks access when a customer session is active.
    Delegates to Django's built-in authentication on the normal path.
    """
    from django.contrib.auth import views as auth_views

    # Already logged in as merchant — go to dashboard
    if request.user.is_authenticated:
        return redirect('dashboard')

    # Block if a customer session is active
    if request.session.get('customer_phone'):
        messages.warning(
            request,
            "You are currently logged in as a customer. "
            "Please log out of your customer account before logging in as a merchant."
        )
        return redirect('customer_dashboard')

    return auth_views.LoginView.as_view(
        template_name='payments/merchant_login.html'
    )(request)


def register(request):
    """Merchant registration with on-chain profile storage."""
    # Block if a customer session is already active
    if request.session.get('customer_phone'):
        messages.warning(
            request,
            "You are currently logged in as a customer. "
            "Please log out of your customer account before registering as a merchant."
        )
        return redirect('customer_dashboard')

    if request.method == 'POST':
        form = MerchantRegistrationForm(request.POST)
        if form.is_valid():
            # 1. Create Django user
            user = form.save()

            # 2. Generate Stellar keypair and fund via Friendbot
            keypair = stellar_utils.generate_keypair()
            if not stellar_utils.fund_account(keypair.public_key):
                messages.error(
                    request,
                    f"Account creation failed — Friendbot could not fund "
                    f"{keypair.public_key}. Please try again."
                )
                user.delete()  # roll back the Django user
                return render(request, 'payments/merchant_register.html', {'form': form})

            # 3. Establish USDC trustline
            try:
                stellar_utils.create_trustline(
                    keypair.secret, 'USDC', settings.USDC_ISSUER
                )
            except Exception as exc:
                logger.warning("Trustline creation failed for %s: %s", keypair.public_key[:8], exc)
                messages.warning(request, f"USDC trustline issue: {exc}")

            # 4. Prepare on-chain profile data
            profile_data = {
                stellar_utils.DATA_KEY_USERNAME: form.cleaned_data['username'],
                stellar_utils.DATA_KEY_BUSINESS: form.cleaned_data['business_name'][:60],
                stellar_utils.DATA_KEY_PHONE:    form.cleaned_data['phone_number'][:60],
                stellar_utils.DATA_KEY_CREATED:  str(int(time.time())),
            }

            # 5. Store profile on Stellar ledger (with retry)
            tx_hash = None
            for attempt in range(3):
                try:
                    tx_hash = stellar_utils.store_merchant_profile(
                        keypair.secret, profile_data
                    )
                    break
                except Exception as exc:
                    if attempt == 2:
                        logger.error("Profile storage failed for new merchant: %s", exc)
                        messages.error(
                            request,
                            "Failed to store your profile on the Stellar blockchain. "
                            "Please contact support."
                        )
                        user.delete()  # roll back
                        return render(request, 'payments/merchant_register.html', {'form': form})
                    time.sleep(2 ** attempt)

            # 6. Persist ONLY the encrypted secret to PostgreSQL
            merchant = Merchant(
                user=user,
                stellar_public_key=keypair.public_key,
                cached_username=form.cleaned_data['username'],
                cached_business_name=form.cleaned_data['business_name'],
            )
            merchant.set_secret_key(keypair.secret)  # encrypts via security.py
            merchant.save()

            # 7. Log in and redirect
            login(request, user)
            messages.success(
                request,
                "Registration successful! Your public profile is now stored on "
                "the Stellar blockchain."
            )
            return redirect('dashboard')
    else:
        form = MerchantRegistrationForm()

    return render(request, 'payments/merchant_register.html', {'form': form})

@login_required
def dashboard(request):
    """Merchant dashboard — fetches live profile from the Stellar ledger."""
    try:
        merchant = request.user.merchant
    except Merchant.DoesNotExist:
        messages.error(request, "Merchant profile not found.")
        return redirect('index')

    # Fetch fresh profile from Stellar and update local cache
    onchain_profile = None
    try:
        onchain_profile = stellar_utils.get_merchant_profile(
            merchant.stellar_public_key
        )
        if onchain_profile:
            merchant.cached_username = onchain_profile.get(stellar_utils.DATA_KEY_USERNAME, merchant.cached_username)
            merchant.cached_business_name = onchain_profile.get(stellar_utils.DATA_KEY_BUSINESS, merchant.cached_business_name)
            merchant.save(update_fields=['cached_username', 'cached_business_name', 'last_synced_at'])
    except Exception as exc:
        logger.error("Failed to fetch Stellar profile for %s: %s", merchant.stellar_public_key[:8], exc)
        messages.warning(request, "Could not sync profile from Stellar. Showing cached data.")

    # Fetch phone separately if we have a full profile
    phone = None
    if onchain_profile:
        phone = onchain_profile.get(stellar_utils.DATA_KEY_PHONE)
    else:
        phone = stellar_utils.get_merchant_phone(merchant.stellar_public_key)

    # Get recent transactions
    transactions = Transaction.objects.filter(
        merchant=merchant
    ).order_by('-created_at')[:10]

    context = {
        'merchant': merchant,
        'onchain_profile': onchain_profile,
        'phone': phone,
        'transactions': transactions,
        'explorer_url': settings.TESTNET_EXPLORER_URL,
        'explorer_account_url': f"{settings.TESTNET_EXPLORER_URL}/account/{merchant.stellar_public_key}",
        'explorer_payments_url': f"{settings.TESTNET_EXPLORER_URL}/account/{merchant.stellar_public_key}?filter=payments",
        'data_keys': stellar_utils.get_all_data_keys(merchant.stellar_public_key)
                     if onchain_profile else [],
    }
    return render(request, 'payments/merchant_dashboard.html', context)

@login_required
def transaction_history(request):
    """Full transaction history for merchant"""
    merchant = request.user.merchant
    transactions = Transaction.objects.filter(merchant=merchant).order_by('-created_at')
    
    context = {
        'transactions': transactions,
        'explorer_url': settings.TESTNET_EXPLORER_URL,
    }
    return render(request, 'payments/transaction_history.html', context)

def payment_form(request):
    """Public page where customer enters merchant username and amount"""
    if request.method == 'POST':
        form = CustomerPaymentForm(request.POST)
        if form.is_valid():
            # Get form data
            username = form.cleaned_data['merchant_username']
            amount_tzs = form.cleaned_data['amount_tzs']
            customer_phone = form.cleaned_data['customer_phone']
            
            # Look up merchant by cached username
            try:
                merchant = Merchant.objects.get(cached_username=username)
            except Merchant.DoesNotExist:
                messages.error(request, "Merchant not found.")
                return render(request, 'payments/payment_form.html', {'form': form})

            # Cross-verify username against on-chain ledger data.
            # Three outcomes:
            #   - on-chain username matches  → verified, proceed silently
            #   - no on-chain data yet       → allow payment, show info notice
            #   - on-chain data exists but differs → block (tampered profile)
            try:
                onchain_username = stellar_utils.get_single_data_entry(
                    merchant.stellar_public_key,
                    stellar_utils.DATA_KEY_USERNAME,
                )
                if onchain_username is None:
                    # Profile not yet pushed to Stellar (pre-migration merchant
                    # or Stellar write failed during registration). Allow payment
                    # but surface a soft notice so the merchant knows to migrate.
                    logger.info(
                        "No on-chain profile for %s — skipping strict verification.",
                        merchant.stellar_public_key[:8],
                    )
                    messages.info(
                        request,
                        "Note: This merchant's profile is not yet verified on the "
                        "Stellar blockchain. Payment will proceed using cached data."
                    )
                elif onchain_username != username:
                    # Data exists on-chain but does not match — hard block.
                    logger.warning(
                        "On-chain username mismatch for %s: expected '%s', got '%s'",
                        merchant.stellar_public_key[:8],
                        username,
                        onchain_username,
                    )
                    messages.error(
                        request,
                        "Merchant verification failed: the on-chain username does not "
                        "match. Please contact support."
                    )
                    return render(request, 'payments/payment_form.html', {'form': form})
                # else: onchain_username == username → verified, no message needed
            except Exception as exc:
                logger.error("On-chain verification error for %s: %s",
                             merchant.stellar_public_key[:8], exc)
                messages.warning(
                    request,
                    "Unable to verify merchant on the blockchain. Proceeding with "
                    "cached data."
                )
            
            # Convert TZS to XLM and round to 7 decimal places for Stellar SDK
            amount_xlm = (amount_tzs / TZS_PER_XLM).quantize(Decimal('0.0000001'))
            
            # Generate memo (phone last 6 digits + time)
            memo = f"{customer_phone[-6:]}{datetime.datetime.now().strftime('%H%M')}"
            
            try:
                # Determine source account for payment
                if request.user.is_authenticated:
                    try:
                        merchant_payer = request.user.merchant
                        from_secret = merchant_payer.get_secret_key()
                        from_public = merchant_payer.stellar_public_key
                        payer_label = f"Merchant: {merchant_payer.cached_business_name or merchant_payer.cached_username}"
                        # use cached fields — business_name is now on Stellar ledger
                    except Merchant.DoesNotExist:
                        # Fallback for generic authenticated users if no merchant profile
                        customer = stellar_utils.get_or_create_customer_account()
                        from_secret = customer.secret
                        from_public = customer.public_key
                        payer_label = "Demo Customer"
                else:
                    # Anonymous payment from demo account
                    customer = stellar_utils.get_or_create_customer_account()
                    from_secret = customer.secret
                    from_public = customer.public_key
                    payer_label = "Demo Customer"
                
                # Check balance before sending payment
                balances = stellar_utils.get_account_balances(from_public)
                xlm_balance = next((b['balance'] for b in balances if b['asset'] == 'XLM'), '0')
                
                if Decimal(xlm_balance) < amount_xlm + Decimal('0.0001'):  # 0.0001 XLM for fee
                    # Only try friendbot funding for demo customer
                    if not request.user.is_authenticated:
                        stellar_utils.fund_account(from_public)
                        # Re-check balance
                        balances = stellar_utils.get_account_balances(from_public)
                        xlm_balance = next((b['balance'] for b in balances if b['asset'] == 'XLM'), '0')
                    
                    if Decimal(xlm_balance) < amount_xlm + Decimal('0.0001'):
                        messages.error(request, f"Insufficient XLM balance for {payer_label}. Required: {amount_xlm + Decimal('0.0001')}, Available: {xlm_balance}")
                        return render(request, 'payments/payment_form.html', {'form': form})

                # Send payment
                tx_hash = stellar_utils.send_xlm_payment(
                    from_secret=from_secret,
                    to_public=merchant.stellar_public_key,
                    amount_xlm=amount_xlm,
                    memo_text=memo
                )
                
                # Record transactions
                # 1. Incoming for recipient merchant
                Transaction.objects.create(
                    merchant=merchant,
                    transaction_hash=tx_hash,
                    amount_tzs=amount_tzs,
                    amount_xlm=amount_xlm,
                    customer_phone=customer_phone,
                    memo=memo,
                    status='completed',
                    direction='inbound'
                )

                # 2. Outgoing for sending merchant (if applicable)
                if request.user.is_authenticated:
                    try:
                        merchant_payer = request.user.merchant
                        # Don't record twice if merchant is paying themselves (unlikely but possible)
                        if merchant_payer != merchant:
                            Transaction.objects.create(
                                merchant=merchant_payer,
                                transaction_hash=tx_hash,
                                amount_tzs=amount_tzs,
                                amount_xlm=amount_xlm,
                                customer_phone=customer_phone,
                                memo=memo,
                                status='completed',
                                direction='outbound'
                            )
                    except Merchant.DoesNotExist:
                        pass
                
                # Success message
                messages.success(request, "Payment sent successfully!")
                
                # Redirect to success page
                return redirect('payment_success', tx_hash=tx_hash)
                
            except Exception as e:
                messages.error(request, f"Payment failed: {str(e)}")
                return render(request, 'payments/payment_form.html', {'form': form})
    else:
        form = CustomerPaymentForm()
        # Pre-fill merchant if in GET params
        merchant_username = request.GET.get('merchant')
        if merchant_username:
            form.fields['merchant_username'].initial = merchant_username
            
    return render(request, 'payments/payment_form.html', {'form': form})

def payment_success(request, tx_hash):
    """Show payment success with transaction details and explorer link"""
    # Get transaction from database
    transaction = get_object_or_404(Transaction, transaction_hash=tx_hash)
    
    # Optionally fetch additional details from Horizon
    tx_details = stellar_utils.get_transaction_from_hash(tx_hash)
    
    context = {
        'transaction': transaction,
        'tx_hash': tx_hash,
        'explorer_tx_url': f"{settings.TESTNET_EXPLORER_URL}/tx/{tx_hash}",
        'explorer_account_url': f"{settings.TESTNET_EXPLORER_URL}/account/{transaction.merchant.stellar_public_key}?filter=payments",
        'tx_details': tx_details,
    }
    return render(request, 'payments/payment_success.html', context)

@login_required
def api_balance(request):
    """AJAX endpoint to get current balance"""
    merchant = request.user.merchant
    balances = stellar_utils.get_account_balances(merchant.stellar_public_key)
    
    from django.http import JsonResponse
    return JsonResponse({'balances': balances})


# ═══════════════════════════════════════════════════════════════════════════════
# Dual-path customer payment flow
# ═══════════════════════════════════════════════════════════════════════════════

def payment_method(request):
    """
    Step 1: customer selects wallet path (Path A) or phone path (Path B).
    Accepts optional ?merchant= and ?amount= GET params to pre-fill downstream.
    """
    form = CustomerPaymentMethodForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        method = form.cleaned_data['payment_method']
        # Carry any merchant/amount query params through the redirect
        qs = request.POST.urlencode() if '' else ''
        merchant_param = request.GET.get('merchant', '')
        amount_param = request.GET.get('amount', '')
        extra = ''
        if merchant_param:
            extra += f'?merchant={merchant_param}'
            if amount_param:
                extra += f'&amount={amount_param}'
        if method == 'wallet':
            return redirect(f'/pay/wallet/{extra}')
        elif method == 'xlm':
            return redirect(f'/pay/{extra}')
        else:
            return redirect(f'/pay/phone/{extra}')
    return render(request, 'payments/payment_method.html', {
        'form': form,
        'merchant': request.GET.get('merchant', ''),
    })


def payment_wallet(request):
    """
    Step 2a: Wallet customer (Path A) — validate Stellar account and generate XDR.

    The merchant's Stellar account sends USDC; here we validate the sender's
    wallet and build an unsigned XDR for the customer to sign externally.
    """
    initial = {}
    if request.GET.get('merchant'):
        initial['merchant_username'] = request.GET['merchant']

    if request.method == 'POST':
        form = WalletPaymentForm(request.POST)
        if not form.is_valid():
            return render(request, 'payments/payment_wallet.html', {'form': form})

        username = form.cleaned_data['merchant_username']
        amount_tzs = form.cleaned_data['amount_tzs']
        customer_public_key = form.cleaned_data['stellar_public_key']
        # Convert TZS → USDC (rate: 1 USDC = 2500 TZS)
        amount_usdc = (amount_tzs / Decimal('2500')).quantize(Decimal('0.0000001'))

        try:
            merchant = Merchant.objects.get(cached_username=username)
        except Merchant.DoesNotExist:
            form.add_error('merchant_username', 'Merchant not found.')
            return render(request, 'payments/payment_wallet.html', {'form': form})

        # Validate the customer's Stellar account
        validation = stellar_utils.validate_account_for_payment(customer_public_key, amount_usdc)
        if not validation['is_valid']:
            messages.error(request, validation['error'])
            return render(request, 'payments/payment_wallet.html', {
                'form': form,
                'validation_details': validation['details'],
            })

        # Generate unsigned XDR
        try:
            memo = f"PAY{customer_public_key[-4:]}{int(timezone.now().timestamp()) % 10000:04d}"
            xdr = stellar_utils.generate_payment_xdr(
                from_account=customer_public_key,
                to_account=merchant.stellar_public_key,
                amount_usdc=str(amount_usdc),
                memo_text=memo[:28],
            )
        except Exception as exc:
            logger.error("XDR generation failed: %s", exc)
            messages.error(request, f"Could not build transaction: {exc}")
            return render(request, 'payments/payment_wallet.html', {'form': form})

        # Store pending payment in session (expires with session)
        request.session['pending_wallet_payment'] = {
            'merchant_id': merchant.id,
            'merchant_public_key': merchant.stellar_public_key,
            'merchant_username': username,
            'amount_usdc': str(amount_usdc),
            'amount_tzs': str(amount_tzs),
            'customer_public_key': customer_public_key,
            'memo': memo,
            'created_at': timezone.now().isoformat(),
        }
        request.session.modified = True

        return render(request, 'payments/payment_sign.html', {
            'transaction_xdr': xdr,
            'amount_tzs': amount_tzs,
            'amount_usdc': amount_usdc,
            'merchant_username': username,
            'merchant_public_key': merchant.stellar_public_key,
            'customer_public_key': customer_public_key,
            'memo': memo,
            'stellar_lab_url': (
                f"https://laboratory.stellar.org/#txsigner"
                f"?xdr={xdr}&network=test"
            ),
        })
    else:
        form = WalletPaymentForm(initial=initial)

    return render(request, 'payments/payment_wallet.html', {'form': form})


def payment_sign(request):
    """
    Step 3a: Customer pastes back the signed XDR from their wallet / Stellar Laboratory.
    We submit it to Horizon and record the transaction.
    """
    pending = request.session.get('pending_wallet_payment')
    if not pending:
        messages.error(request, "Payment session expired. Please start again.")
        return redirect('payment_form')

    if request.method == 'POST':
        signed_xdr = request.POST.get('signed_transaction', '').strip()
        if not signed_xdr:
            messages.error(request, "Please paste the signed transaction XDR.")
            return render(request, 'payments/payment_sign.html', {'pending': pending})

        try:
            tx_hash = stellar_utils.submit_signed_transaction(signed_xdr)
        except Exception as exc:
            logger.error("Signed XDR submission failed: %s", exc)
            messages.error(request, f"Transaction rejected by Stellar: {exc}")
            return render(request, 'payments/payment_sign.html', {'pending': pending})

        # Upsert wallet customer record
        customer, _ = Customer.objects.get_or_create(
            stellar_public_key=pending['customer_public_key'],
            defaults={'customer_type': 'wallet', 'is_active': True},
        )

        merchant = Merchant.objects.get(id=pending['merchant_id'])
        Transaction.objects.create(
            merchant=merchant,
            transaction_hash=tx_hash,
            amount_tzs=pending['amount_tzs'],
            amount_usdc=pending['amount_usdc'],
            customer_wallet_public_key=pending['customer_public_key'],
            memo=pending['memo'],
            status='completed',
            direction='inbound',
        )

        del request.session['pending_wallet_payment']
        request.session.modified = True

        messages.success(request, "Payment completed successfully!")
        return redirect('payment_success', tx_hash=tx_hash)

    return render(request, 'payments/payment_sign.html', {'pending': pending})


def payment_phone(request):
    """
    Step 2b: Phone customer (Path B) — pay from the pooled master account.

    Customer identifies by phone number; their off-chain USDC balance is
    debited and a real Stellar USDC payment is sent from the master account.
    """
    initial = {}
    if request.GET.get('merchant'):
        initial['merchant_username'] = request.GET['merchant']

    if request.method == 'POST':
        form = PhonePaymentForm(request.POST)
        if not form.is_valid():
            return render(request, 'payments/payment_phone.html', {'form': form})

        username = form.cleaned_data['merchant_username']
        amount_tzs = form.cleaned_data['amount_tzs']
        customer_phone = form.cleaned_data['customer_phone']
        amount_usdc = (amount_tzs / Decimal('2500')).quantize(Decimal('0.0000001'))

        try:
            merchant = Merchant.objects.get(cached_username=username)
        except Merchant.DoesNotExist:
            form.add_error('merchant_username', 'Merchant not found.')
            return render(request, 'payments/payment_phone.html', {'form': form})

        # Get or create app customer
        customer, created = _get_or_create_app_customer(customer_phone)

        if customer.balance_usdc < amount_usdc:
            messages.error(
                request,
                f"Insufficient balance. You have {customer.balance_usdc:.2f} USDC, "
                f"need {amount_usdc:.2f} USDC. Please deposit funds first."
            )
            return render(request, 'payments/payment_phone.html', {
                'form': form,
                'customer_balance': customer.balance_usdc,
                'required_usdc': amount_usdc,
            })

        try:
            tx_hash = _process_app_customer_payment(
                customer=customer,
                merchant=merchant,
                amount_usdc=amount_usdc,
                amount_tzs=amount_tzs,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            return render(request, 'payments/payment_phone.html', {'form': form})
        except Exception as exc:
            logger.error("Phone payment failed for %s: %s", customer_phone, exc)
            messages.error(request, f"Payment failed: {exc}")
            return render(request, 'payments/payment_phone.html', {'form': form})

        messages.success(request, f"Payment successful! New balance: {customer.balance_usdc:.2f} USDC")
        return redirect('payment_success', tx_hash=tx_hash)
    else:
        form = PhonePaymentForm(initial=initial)

    return render(request, 'payments/payment_phone.html', {'form': form})


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_or_create_app_customer(phone_number: str):
    """Get or create a phone-based (app) Customer, generating a unique memo."""
    try:
        customer = Customer.objects.get(phone_number=phone_number, customer_type='app')
        return customer, False
    except Customer.DoesNotExist:
        memo = Customer.generate_memo()
        customer = Customer.objects.create(
            customer_type='app',
            phone_number=phone_number,
            stellar_memo=memo,
            balance_usdc=Decimal('0'),
        )
        return customer, True


def _process_app_customer_payment(customer, merchant, amount_usdc, amount_tzs):
    """
    Atomically debit the app customer's balance and send USDC from the master account.
    Returns the Stellar transaction hash.
    """
    from django.db import transaction as db_transaction
    from decimal import Decimal as _Decimal

    with db_transaction.atomic():
        # Lock the customer row to prevent double-spends
        customer = Customer.objects.select_for_update().get(id=customer.id)

        if customer.balance_usdc < amount_usdc:
            raise ValueError("Insufficient balance.")

        # Send USDC from master pooled account
        master_keypair = stellar_utils.get_master_customer_keypair()
        tx_hash = stellar_utils.send_usdc_payment(
            from_secret=master_keypair.secret,
            to_public=merchant.stellar_public_key,
            amount_usdc=float(amount_usdc),
            memo_text=customer.stellar_memo,
        )

        # Debit customer balance
        customer.balance_usdc = customer.balance_usdc - _Decimal(str(amount_usdc))
        customer.save(update_fields=['balance_usdc', 'last_seen'])

        # Record transaction
        Transaction.objects.create(
            merchant=merchant,
            customer_app=customer,
            transaction_hash=tx_hash,
            amount_tzs=amount_tzs,
            amount_usdc=amount_usdc,
            customer_phone=customer.phone_number or '',
            memo=customer.stellar_memo,
            status='completed',
            direction='inbound',
        )

        return tx_hash


# ═══════════════════════════════════════════════════════════════════════════════
# Merchant withdrawal flow (separate from customer withdrawal)
# ═══════════════════════════════════════════════════════════════════════════════

TZS_PER_USDC = Decimal('2500')

@login_required
def merchant_withdraw_request(request):
    """
    Merchant requests cash-out of their earned Stellar balance.
    Staff reviews and disburses via mobile money / bank transfer.
    """
    from .models import MerchantWithdrawal
    try:
        merchant = request.user.merchant
    except Merchant.DoesNotExist:
        messages.error(request, "Merchant profile not found.")
        return redirect('index')

    # Fetch live balances to show merchant what they have
    balances = []
    try:
        balances = stellar_utils.get_account_balances(merchant.stellar_public_key)
    except Exception:
        pass

    if request.method == 'POST':
        amount_tzs_raw = request.POST.get('amount_tzs', '').strip()
        currency = request.POST.get('currency', 'USDC')
        payout_phone = request.POST.get('payout_phone', '').strip()
        payout_method = request.POST.get('payout_method', 'mobile_money')

        try:
            amount_tzs = Decimal(amount_tzs_raw)
        except Exception:
            messages.error(request, "Invalid amount.")
            return render(request, 'payments/merchant_withdrawal_request.html', {
                'merchant': merchant, 'balances': balances,
            })

        if amount_tzs < Decimal('1000'):
            messages.error(request, "Minimum withdrawal is 1,000 TZS.")
            return render(request, 'payments/merchant_withdrawal_request.html', {
                'merchant': merchant, 'balances': balances,
            })

        if currency == 'USDC':
            amount_stellar = (amount_tzs / TZS_PER_USDC).quantize(Decimal('0.0000001'))
        else:
            amount_stellar = (amount_tzs / TZS_PER_XLM).quantize(Decimal('0.0000001'))

        MerchantWithdrawal.objects.create(
            merchant=merchant,
            amount_tzs=amount_tzs,
            amount_stellar=amount_stellar,
            currency=currency,
            payout_phone=payout_phone,
            payout_method=payout_method,
            status='requested',
        )

        messages.success(
            request,
            f"Withdrawal request for {amount_tzs:,.0f} TZS ({amount_stellar} {currency}) "
            "submitted successfully. Staff will process it within 24 hours."
        )
        return redirect('merchant_withdraw_history')

    return render(request, 'payments/merchant_withdrawal_request.html', {
        'merchant': merchant,
        'balances': balances,
        'tzs_per_usdc': TZS_PER_USDC,
        'tzs_per_xlm': TZS_PER_XLM,
    })


@login_required
def merchant_withdraw_history(request):
    """Merchant's withdrawal request history."""
    from .models import MerchantWithdrawal
    try:
        merchant = request.user.merchant
    except Merchant.DoesNotExist:
        return redirect('index')

    withdrawals = MerchantWithdrawal.objects.filter(merchant=merchant).order_by('-requested_at')
    return render(request, 'payments/merchant_withdrawal_history.html', {
        'merchant': merchant,
        'withdrawals': withdrawals,
    })

