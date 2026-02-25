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

from .models import Merchant, Transaction
from .forms import MerchantRegistrationForm, CustomerPaymentForm
from . import stellar_utils

# Fixed exchange rate for demo (1 XLM = 300 TZS)
TZS_PER_XLM = Decimal('300')

def index(request):
    """Landing page"""
    return render(request, 'payments/index.html')

def register(request):
    """Merchant registration with on-chain profile storage."""
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

            # Cross-verify username against on-chain ledger data
            try:
                is_verified = stellar_utils.verify_merchant_username(
                    merchant.stellar_public_key, username
                )
                if not is_verified:
                    logger.warning(
                        "Username mismatch for account %s (claimed: %s)",
                        merchant.stellar_public_key[:8],
                        username,
                    )
                    messages.error(
                        request,
                        "Merchant verification failed. "
                        "Please try again or contact support."
                    )
                    return render(request, 'payments/payment_form.html', {'form': form})
            except Exception as exc:
                logger.error("On-chain verification error: %s", exc)
                messages.warning(
                    request,
                    "Unable to verify merchant on the blockchain. Proceed with caution."
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
