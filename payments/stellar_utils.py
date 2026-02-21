from stellar_sdk import Server, Keypair, TransactionBuilder, Asset, Network
from stellar_sdk.exceptions import NotFoundError
from django.conf import settings
import requests
import os

def get_server():
    return Server(settings.STELLAR_HORIZON_URL)

def get_network_passphrase():
    return settings.STELLAR_NETWORK_PASSPHRASE

def generate_keypair():
    return Keypair.random()

def fund_account(public_key):
    """Fund account on testnet using Friendbot"""
    url = f"{settings.FRIENDBOT_URL}?addr={public_key}"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            # Poll for account existence (up to 30 seconds)
            for _ in range(6):
                if account_exists(public_key):
                    return True
                import time
                time.sleep(5)
        return False
    except Exception as e:
        print(f"Error funding account: {e}")
        return False

def account_exists(public_key):
    """Check if account exists on network"""
    server = get_server()
    try:
        server.accounts().account_id(public_key).call()
        return True
    except NotFoundError:
        return False

def create_trustline(secret_key, asset_code, asset_issuer):
    """Establish trustline to an asset"""
    server = get_server()
    keypair = Keypair.from_secret(secret_key)
    account = server.load_account(keypair.public_key)
    
    asset = Asset(asset_code, asset_issuer)
    
    transaction = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=get_network_passphrase(),
            base_fee=100
        )
        .append_change_trust_op(asset=asset)
        .set_timeout(30)
        .build()
    )
    
    transaction.sign(keypair)
    response = server.submit_transaction(transaction)
    return response['hash']

def send_xlm_payment(from_secret, to_public, amount_xlm, memo_text):
    """Send XLM (native) payment with memo"""
    server = get_server()
    from_keypair = Keypair.from_secret(from_secret)
    from_account = server.load_account(from_keypair.public_key)
    
    transaction = (
        TransactionBuilder(
            source_account=from_account,
            network_passphrase=get_network_passphrase(),
            base_fee=100
        )
        .append_payment_op(
            destination=to_public,
            asset=Asset.native(),
            amount=str(amount_xlm)
        )
        .add_text_memo(memo_text)
        .set_timeout(30)
        .build()
    )
    
    transaction.sign(from_keypair)
    response = server.submit_transaction(transaction)
    return response['hash']

def send_usdc_payment(from_secret, to_public, amount_usdc, memo_text):
    """Send USDC payment with memo"""
    server = get_server()
    from_keypair = Keypair.from_secret(from_secret)
    from_account = server.load_account(from_keypair.public_key)
    
    # USDC asset
    usdc_asset = Asset('USDC', settings.USDC_ISSUER)
    
    transaction = (
        TransactionBuilder(
            source_account=from_account,
            network_passphrase=get_network_passphrase(),
            base_fee=100
        )
        .append_payment_op(
            destination=to_public,
            asset=usdc_asset,
            amount=str(amount_usdc)
        )
        .add_text_memo(memo_text)  # Add memo for transaction identification
        .set_timeout(30)
        .build()
    )
    
    transaction.sign(from_keypair)
    response = server.submit_transaction(transaction)
    return response['hash']

def get_account_balances(public_key):
    """Return list of balances for an account"""
    server = get_server()
    try:
        account = server.accounts().account_id(public_key).call()
        balances = []
        for balance in account['balances']:
            if balance['asset_type'] == 'native':
                balances.append({'asset': 'XLM', 'balance': balance['balance']})
            else:
                balances.append({
                    'asset': balance.get('asset_code', 'Unknown'),
                    'issuer': balance.get('asset_issuer', ''),
                    'balance': balance['balance']
                })
        return balances
    except NotFoundError:
        return None

def get_or_create_master_funding_account():
    """
    Get or create a master account used to fund customers with USDC.
    """
    master_secret = os.getenv('MASTER_USDC_SECRET')
    if master_secret:
        return Keypair.from_secret(master_secret)
    
    # Create new master account
    master = generate_keypair()
    if not fund_account(master.public_key):
        raise Exception(f"Failed to fund master account {master.public_key} using Friendbot.")
    
    # Create trustline to USDC (it needs trustline to hold USDC)
    create_trustline(master.secret, 'USDC', settings.USDC_ISSUER)
    
    # Append to .env (simple approach for demo)
    try:
        with open('.env', 'a') as f:
            f.write(f"\nMASTER_USDC_SECRET={master.secret}\n")
    except Exception as e:
        print(f"Warning: Could not save MASTER_USDC_SECRET to .env: {e}")
        
    return master

def fund_customer_with_usdc(customer_public_key, amount=100):
    """
    Send USDC from master funding account to customer account.
    """
    from decimal import Decimal
    # Ensure amount has at most 7 decimal places for Stellar SDK
    if not isinstance(amount, Decimal):
        amount = Decimal(str(amount))
    amount = amount.quantize(Decimal('0.0000001'))

    master = get_or_create_master_funding_account()
    
    try:
        # Check master USDC balance first
        master_balances = get_account_balances(master.public_key)
        master_usdc = next((b['balance'] for b in master_balances if b['asset'] == 'USDC'), '0')
        
        if Decimal(master_usdc) < amount:
            raise Exception(f"Master account {master.public_key} has insufficient USDC ({master_usdc}). "
                            "Please fund the master account with USDC first.")
        
        tx_hash = send_usdc_payment(
            from_secret=master.secret,
            to_public=customer_public_key,
            amount_usdc=amount,
            memo_text="FUNDING_USDC"
        )
        return tx_hash
    except Exception as e:
        print(f"Error funding customer with USDC: {e}")
        raise e

def get_or_create_customer_account():
    """
    Get or create the demo customer account.
    """
    customer_secret = os.getenv('CUSTOMER_SECRET')
    if customer_secret:
        customer = Keypair.from_secret(customer_secret)
    else:
        # Create new customer account
        customer = generate_keypair()
        if not fund_account(customer.public_key):
            raise Exception(f"Failed to fund customer account {customer.public_key} using Friendbot.")
        
        # Append to .env (simple approach for demo)
        try:
            with open('.env', 'a') as f:
                f.write(f"\nCUSTOMER_SECRET={customer.secret}\n")
        except Exception as e:
            print(f"Warning: Could not save CUSTOMER_SECRET to .env: {e}")
    
    # Check XLM balance (Friendbot provides it automatically)
    try:
        balances = get_account_balances(customer.public_key)
        xlm_balance = next((b['balance'] for b in balances if b['asset'] == 'XLM'), '0')
        
        if float(xlm_balance) < 1.0:
            print(f"Customer XLM balance low ({xlm_balance}). Attempting to re-fund...")
            fund_account(customer.public_key)
    except Exception as e:
        print(f"Warning: Could not check/fund customer XLM balance: {e}")
        
    return customer

def get_transaction_from_hash(tx_hash):
    """Fetch transaction details from Horizon"""
    server = get_server()
    try:
        tx = server.transactions().transaction(tx_hash).call()
        return tx
    except:
        return None
