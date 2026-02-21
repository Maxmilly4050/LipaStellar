from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from decimal import Decimal
import datetime

from .models import Merchant, Transaction
from .forms import MerchantRegistrationForm, CustomerPaymentForm
from . import stellar_utils

# Fixed exchange rate for demo (1 XLM = 300 TZS)
TZS_PER_XLM = Decimal('300')

def index(request):
    """Landing page"""
    return render(request, 'payments/index.html')

def register(request):
    """Merchant registration"""
    if request.method == 'POST':
        form = MerchantRegistrationForm(request.POST)
        if form.is_valid():
            # Create Django user
            user = form.save()
            
            # Generate Stellar account
            keypair = stellar_utils.generate_keypair()
            
            # Fund account on testnet and wait for it to be indexed
            if not stellar_utils.fund_account(keypair.public_key):
                messages.error(request, f"Account creation failed. Friendbot was unable to fund {keypair.public_key}. Please try again.")
                return render(request, 'payments/merchant_register.html', {'form': form})
            
            # Create merchant record
            merchant = Merchant(
                user=user,
                business_name=form.cleaned_data['business_name'],
                phone_number=form.cleaned_data['phone_number'],
                username=form.cleaned_data['username'],
                stellar_public_key=keypair.public_key
            )
            merchant.set_secret_key(keypair.secret)  # Encrypt and store
            merchant.save()
            
            # Log the user in
            login(request, user)
            messages.success(request, "Registration successful! Your Stellar wallet has been created and funded with Testnet XLM.")
            return redirect('dashboard')
    else:
        form = MerchantRegistrationForm()
    
    return render(request, 'payments/merchant_register.html', {'form': form})

@login_required
def dashboard(request):
    """Merchant dashboard"""
    try:
        merchant = request.user.merchant
    except Merchant.DoesNotExist:
        messages.error(request, "Merchant profile not found.")
        return redirect('index')
    
    # Get recent transactions
    transactions = Transaction.objects.filter(merchant=merchant).order_by('-created_at')[:10]
    
    context = {
        'merchant': merchant,
        'transactions': transactions,
        'explorer_url': settings.TESTNET_EXPLORER_URL,
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
            
            # Look up merchant
            merchant = Merchant.objects.get(username=username)
            
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
                        payer_label = f"Merchant: {merchant_payer.business_name}"
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
                
                # Record transaction
                Transaction.objects.create(
                    merchant=merchant,
                    transaction_hash=tx_hash,
                    amount_tzs=amount_tzs,
                    amount_xlm=amount_xlm,
                    customer_phone=customer_phone,
                    memo=memo,
                    status='completed'
                )
                
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
