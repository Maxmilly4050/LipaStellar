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
    response = requests.get(url)
    return response.status_code == 200

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

def get_or_create_customer_account():
    """
    Get or create the demo customer account.
    """
    customer_secret = os.getenv('CUSTOMER_SECRET')
    if customer_secret:
        return Keypair.from_secret(customer_secret)
    
    # Create new customer account
    customer = generate_keypair()
    fund_account(customer.public_key)
    # Wait for account to be created
    import time
    time.sleep(5)
    # Create trustline to USDC
    create_trustline(customer.secret, 'USDC', settings.USDC_ISSUER)
    
    # We should ideally save this to .env or DB, but for demo we just return it
    # In a real app, this would be a persistent account
    return customer

def get_transaction_from_hash(tx_hash):
    """Fetch transaction details from Horizon"""
    server = get_server()
    try:
        tx = server.transactions().transaction(tx_hash).call()
        return tx
    except:
        return None
