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

# Fixed exchange rate for demo (1 USDC = 2500 TZS)
TZS_PER_USDC = Decimal('2500')

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
            
            # Fund account on testnet
            stellar_utils.fund_account(keypair.public_key)
            
            # Wait for account to be created by Friendbot
            import time
            time.sleep(5)
            
            # Create trustline to USDC
            try:
                stellar_utils.create_trustline(keypair.secret, 'USDC', settings.USDC_ISSUER)
            except Exception as e:
                messages.warning(request, f"Trustline creation issue: {str(e)}")
            
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
            messages.success(request, "Registration successful! Your Stellar wallet has been created and funded with Testnet XLM/USDC.")
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
            
            # Convert TZS to USDC
            amount_usdc = amount_tzs / TZS_PER_USDC
            
            # Generate memo (phone last 6 digits + time)
            memo = f"{customer_phone[-6:]}{datetime.datetime.now().strftime('%H%M')}"
            
            try:
                # Get customer account (create if doesn't exist)
                customer = stellar_utils.get_or_create_customer_account()
                
                # Send payment
                tx_hash = stellar_utils.send_usdc_payment(
                    from_secret=customer.secret,
                    to_public=merchant.stellar_public_key,
                    amount_usdc=float(amount_usdc),
                    memo_text=memo
                )
                
                # Record transaction
                Transaction.objects.create(
                    merchant=merchant,
                    transaction_hash=tx_hash,
                    amount_tzs=amount_tzs,
                    amount_usdc=amount_usdc,
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
