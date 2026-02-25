"""
Stellar utilities for LipaStellar.

On-chain storage model
----------------------
Merchant public identity is stored on the Stellar ledger as ManageData entries:

  Key            Max length    Description
  ─────────────  ──────────    ───────────────────────────────────────────
  "username"     50 chars      Merchant username  (e.g. @mama_cafe)
  "business"     60 chars      Business / trading name
  "phone"        60 chars      Contact phone number
  "created"      10 chars      Unix timestamp of registration
  "website"      64 chars      Optional website URL
  "desc"         64 chars      Optional short description

Stellar restricts every ManageData value to 64 bytes.  Values exceeding this
limit are silently truncated with a warning logged.

Data retrieval
--------------
  get_merchant_profile(public_key)          → full dict of all entries
  get_single_data_entry(public_key, key)    → single decoded value
  get_merchant_username / _business_name / _phone  → convenience wrappers
  verify_merchant_username(pub, username)   → boolean cross-check
  get_all_data_keys(public_key)             → list of present keys
  account_has_data_entry(pub, key)          → boolean existence check
"""

import base64
import logging
import os
import time
from decimal import Decimal

import requests
from django.conf import settings
from stellar_sdk import Asset, Keypair, Network, Server, TransactionBuilder
from stellar_sdk.exceptions import BadRequestError, NotFoundError

logger = logging.getLogger(__name__)

# ── On-chain data key constants ──────────────────────────────────────────────
DATA_KEY_USERNAME = "username"
DATA_KEY_BUSINESS = "business"
DATA_KEY_PHONE    = "phone"
DATA_KEY_CREATED  = "created"
DATA_KEY_WEBSITE  = "website"
DATA_KEY_DESC     = "desc"

# Maximum bytes per ManageData value (Stellar protocol limit)
_MAX_DATA_BYTES = 64


def get_server() -> Server:
    return Server(settings.STELLAR_HORIZON_URL)


def get_network_passphrase() -> str:
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


# ═══════════════════════════════════════════════════════════════════════════════
# ManageData — on-chain storage for merchant profiles
# ═══════════════════════════════════════════════════════════════════════════════

# ── Storage ──────────────────────────────────────────────────────────────────

def store_merchant_profile(secret_key: str, profile_data: dict) -> str:
    """
    Store merchant profile fields on the Stellar ledger using ManageData ops.

    Each key/value pair in *profile_data* becomes a separate ManageData entry
    on the merchant's Stellar account.  Values longer than 64 bytes are
    automatically truncated (Stellar protocol limit).

    Args:
        secret_key:   Merchant's Stellar secret key (decrypted, used briefly).
        profile_data: Dict mapping data-key constants to string values, e.g.:
                        {DATA_KEY_USERNAME: "mama_cafe", DATA_KEY_BUSINESS: "Mama Café", …}

    Returns:
        transaction_hash of the submitted transaction.

    Raises:
        ValueError:  No valid entries, or insufficient XLM reserve.
        NotFoundError: Account not on the network yet.
        Exception:   Network / Horizon errors after all retries exhausted.
    """
    server = get_server()
    keypair = Keypair.from_secret(secret_key)

    try:
        account = server.load_account(keypair.public_key)
    except NotFoundError:
        raise Exception(
            f"Stellar account {keypair.public_key[:8]}… not found. "
            "Ensure it has been funded via Friendbot before registration."
        )

    # Validate and prepare data entries
    data_entries: list[tuple[str, str]] = []
    warnings: list[str] = []

    for key, value in profile_data.items():
        if value is None:
            continue
        str_value = str(value)
        encoded = str_value.encode("utf-8")
        if len(encoded) > _MAX_DATA_BYTES:
            truncated = encoded[:_MAX_DATA_BYTES].decode("utf-8", errors="ignore")
            warnings.append(f"Field '{key}' truncated from {len(encoded)} to {_MAX_DATA_BYTES} bytes")
            str_value = truncated
        data_entries.append((key, str_value))

    if not data_entries:
        raise ValueError("No valid data to store — profile_data was empty or all-None.")

    # Verify sufficient XLM for new reserve requirements
    account_info = server.accounts().account_id(keypair.public_key).call()
    current_balance = 0.0
    for bal in account_info["balances"]:
        if bal["asset_type"] == "native":
            current_balance = float(bal["balance"])
            break

    existing_entries = len(account_info.get("data", {}))
    new_entries = sum(
        1 for k, _ in data_entries
        if k not in account_info.get("data", {})
    )
    # Each new ManageData entry raises the minimum reserve by 0.5 XLM
    extra_reserve_needed = 0.5 * new_entries
    buffer = 1.0  # Always keep 1 XLM free for fees
    if current_balance < extra_reserve_needed + buffer:
        raise ValueError(
            f"Insufficient XLM balance ({current_balance:.7f} XLM). "
            f"Need at least {extra_reserve_needed + buffer:.1f} XLM "
            f"({new_entries} new entries × 0.5 XLM reserve + {buffer} XLM buffer)."
        )

    # Build transaction — all ManageData ops in a single transaction
    tx_builder = TransactionBuilder(
        source_account=account,
        network_passphrase=get_network_passphrase(),
        base_fee=100,
    )
    for key, value in data_entries:
        tx_builder.append_manage_data_op(data_name=key, data_value=value.encode("utf-8"))
    tx_builder.set_timeout(30)
    transaction = tx_builder.build()
    transaction.sign(keypair)

    # Submit with exponential-backoff retries
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = server.submit_transaction(transaction)
            if warnings:
                logger.warning("store_merchant_profile warnings: %s", warnings)
            logger.info(
                "Stored profile for %s… tx=%s",
                keypair.public_key[:8],
                response["hash"][:8],
            )
            return response["hash"]
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                logger.warning("Submit attempt %d failed (%s), retrying in %ds…", attempt + 1, exc, wait)
                time.sleep(wait)

    raise last_exc  # type: ignore[misc]


# ── Retrieval — full profile ──────────────────────────────────────────────────

def get_merchant_profile(public_key: str) -> dict | None:
    """
    Retrieve all ManageData entries from a Stellar account.

    Horizon stores values as base64; this function decodes them back to UTF-8
    strings.  Returns *None* if the account does not exist.

    Usage:
        profile = get_merchant_profile(merchant.stellar_public_key)
        username = profile.get(DATA_KEY_USERNAME)
        business = profile.get(DATA_KEY_BUSINESS)

    Returns:
        Dict mapping key → decoded string, or {} if no data entries, or None
        if the account is not found.
    """
    server = get_server()
    try:
        account = server.accounts().account_id(public_key).call()
        raw_data: dict = account.get("data", {})

        if not raw_data:
            logger.info("No ManageData entries on account %s…", public_key[:8])
            return {}

        profile: dict[str, str | None] = {}
        for key, value_b64 in raw_data.items():
            try:
                decoded_bytes = base64.b64decode(value_b64)
                profile[key] = decoded_bytes.decode("utf-8")
                logger.debug("Decoded '%s': %s…", key, profile[key][:20])
            except UnicodeDecodeError:
                profile[key] = "[binary data]"
            except Exception as exc:
                logger.warning("Failed to decode ManageData key '%s': %s", key, exc)
                profile[key] = None

        return profile

    except NotFoundError:
        logger.error("Account not found on Horizon: %s", public_key)
        return None
    except Exception as exc:
        logger.error("Error fetching account %s…: %s", public_key[:8], exc)
        return None


# ── Retrieval — single entry ──────────────────────────────────────────────────

def get_single_data_entry(public_key: str, key_name: str) -> str | None:
    """
    Retrieve a single ManageData value by key name.

    Hits the Horizon ``GET /accounts/{id}/data/{key}`` REST endpoint directly
    via requests, because stellar-sdk v10 removed the .data() helper from
    AccountsCallBuilder.

    Args:
        public_key: Stellar G-address of the merchant.
        key_name:   One of the DATA_KEY_* constants (or any custom key).

    Returns:
        Decoded UTF-8 string, or None if the key does not exist.
    """
    horizon_url = settings.STELLAR_HORIZON_URL.rstrip("/")
    url = f"{horizon_url}/accounts/{public_key}/data/{key_name}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        value_b64 = resp.json().get("value")
        if not value_b64:
            return None
        return base64.b64decode(value_b64).decode("utf-8")
    except requests.exceptions.HTTPError:
        return None
    except Exception as exc:
        logger.error("Error fetching '%s' for %s…: %s", key_name, public_key[:8], exc)
        return None


# ── Retrieval — convenience wrappers ─────────────────────────────────────────

def get_merchant_username(public_key: str) -> str | None:
    """Return the on-chain username for *public_key*, or None."""
    return get_single_data_entry(public_key, DATA_KEY_USERNAME)


def get_merchant_business_name(public_key: str) -> str | None:
    """Return the on-chain business name for *public_key*, or None."""
    return get_single_data_entry(public_key, DATA_KEY_BUSINESS)


def get_merchant_phone(public_key: str) -> str | None:
    """Return the on-chain phone number for *public_key*, or None."""
    return get_single_data_entry(public_key, DATA_KEY_PHONE)


def verify_merchant_username(public_key: str, expected_username: str) -> bool:
    """
    Cross-check that the on-chain username matches *expected_username*.

    Returns False if the key is missing or the values differ.
    """
    actual = get_single_data_entry(public_key, DATA_KEY_USERNAME)
    return actual == expected_username


def get_all_data_keys(public_key: str) -> list[str]:
    """
    Return the list of all ManageData key names present on *public_key*.
    Useful for debugging and dashboard display.
    """
    server = get_server()
    try:
        account = server.accounts().account_id(public_key).call()
        return list(account.get("data", {}).keys())
    except Exception as exc:
        logger.error("Error fetching data keys for %s…: %s", public_key[:8], exc)
        return []


def account_has_data_entry(public_key: str, key_name: str) -> bool:
    """Return True if *key_name* exists on the account's ManageData."""
    return get_single_data_entry(public_key, key_name) is not None


# ── Update ────────────────────────────────────────────────────────────────────

def update_merchant_profile(secret_key: str, updates_dict: dict) -> str:
    """
    Update specific ManageData fields without touching others.

    Pass ``None`` as a value to delete that key from the ledger.

    Args:
        secret_key:   Merchant's Stellar secret (decrypted, used briefly).
        updates_dict: Dict of {key: new_value_or_None}.

    Returns:
        transaction_hash.
    """
    server = get_server()
    keypair = Keypair.from_secret(secret_key)
    account = server.load_account(keypair.public_key)

    tx_builder = TransactionBuilder(
        source_account=account,
        network_passphrase=get_network_passphrase(),
        base_fee=100,
    )

    for key, value in updates_dict.items():
        if value is None:
            # Setting data_value=None removes the ManageData entry
            tx_builder.append_manage_data_op(data_name=key, data_value=None)
        else:
            str_value = str(value).encode("utf-8")[:_MAX_DATA_BYTES].decode("utf-8", errors="ignore")
            tx_builder.append_manage_data_op(
                data_name=key,
                data_value=str_value.encode("utf-8"),
            )

    tx_builder.set_timeout(30)
    transaction = tx_builder.build()
    transaction.sign(keypair)

    response = server.submit_transaction(transaction)
    logger.info(
        "Updated profile for %s… fields=%s tx=%s",
        keypair.public_key[:8],
        list(updates_dict.keys()),
        response["hash"][:8],
    )
    return response["hash"]


# ── XLM reserve utilities ─────────────────────────────────────────────────────

def has_sufficient_xlm_for_data(public_key: str, num_new_entries: int = 1) -> bool:
    """
    Check whether *public_key* can afford *num_new_entries* new ManageData ops.

    Each new entry increases the minimum balance by 0.5 XLM.

    Returns:
        True if the account has enough XLM (with a 0.5 XLM safety buffer).
    """
    server = get_server()
    try:
        account = server.accounts().account_id(public_key).call()
        current_balance = 0.0
        for bal in account["balances"]:
            if bal["asset_type"] == "native":
                current_balance = float(bal["balance"])
                break

        existing_entries = len(account.get("data", {}))
        # Base minimum reserve = 0.5 XLM (account) + 0.5 per existing entry
        required = 0.5 * (1 + existing_entries + num_new_entries) + 0.5  # 0.5 buffer
        return current_balance >= required

    except Exception as exc:
        logger.error("Error checking XLM balance for %s…: %s", public_key[:8], exc)
        return False


def get_account_data_summary(public_key: str) -> str:
    """
    Return a human-readable string of all ManageData entries.
    Useful for CLI debugging and management commands.
    """
    profile = get_merchant_profile(public_key)
    if not profile:
        return "No ManageData entries found."
    return "\n".join(f"  {k}: {v}" for k, v in profile.items())


# ═══════════════════════════════════════════════════════════════════════════════
# Account Validation — for dual-path customer onboarding
# ═══════════════════════════════════════════════════════════════════════════════

def validate_stellar_public_key(public_key: str) -> tuple[bool, str | None]:
    """
    Validate the format of a Stellar public key (G-address).

    Returns:
        (True, None) if valid.
        (False, error_message) if invalid.
    """
    if not public_key:
        return False, "Public key is required."
    if not public_key.startswith('G'):
        return False, "Stellar public keys must start with 'G'."
    if len(public_key) != 56:
        return False, f"Stellar public keys must be 56 characters (got {len(public_key)})."
    try:
        Keypair.from_public_key(public_key)
        return True, None
    except Exception:
        return False, "Invalid Stellar public key format."


def check_usdc_trustline(
    public_key: str,
    issuer: str | None = None,
) -> tuple[bool, str, str | None]:
    """
    Check whether an account has a USDC trustline.

    Args:
        public_key: Stellar G-address to inspect.
        issuer:     USDC issuer address. Defaults to settings.USDC_ISSUER.

    Returns:
        (has_trustline, balance_str, error_message)
        balance_str is '0' when no trustline exists.
    """
    if issuer is None:
        issuer = settings.USDC_ISSUER
    try:
        balances = get_account_balances(public_key)
        if balances is None:
            return False, '0', "Account not found on network."
        for b in balances:
            if b.get('asset') == 'USDC' and b.get('issuer') == issuer:
                return True, b['balance'], None
        return False, '0', None
    except Exception as exc:
        return False, '0', str(exc)


def get_minimum_account_requirements() -> dict:
    """
    Return the minimum XLM reserve requirements for a Stellar account.
    Values are in XLM.
    """
    return {
        'base_reserve': 0.5,
        'per_entry_reserve': 0.5,
        'minimum_balance': 1.0,   # base_reserve × 2 subentries (account itself + 1 trustline)
        'transaction_fee': 0.0001,
        'recommended_minimum': 2.0,
    }


def validate_account_for_payment(
    public_key: str,
    required_amount_usdc: "Decimal",
) -> dict:
    """
    Comprehensive pre-payment validation for a wallet customer.

    Checks:
    1. Public key format
    2. Account exists on network
    3. Has USDC trustline
    4. Sufficient USDC balance
    5. Sufficient XLM for transaction fee

    Returns:
        {
          'is_valid': bool,
          'error': str | None,
          'details': {
              'account_exists': bool,
              'has_trustline': bool,
              'usdc_balance': str,
              'xlm_balance': str,
              'required_usdc': str,
          }
        }
    """
    from decimal import Decimal as _Decimal

    details: dict = {
        'account_exists': False,
        'has_trustline': False,
        'usdc_balance': '0',
        'xlm_balance': '0',
        'required_usdc': str(required_amount_usdc),
    }

    # 1. Format validation
    fmt_ok, fmt_err = validate_stellar_public_key(public_key)
    if not fmt_ok:
        return {'is_valid': False, 'error': fmt_err, 'details': details}

    # 2. Account existence
    if not account_exists(public_key):
        return {
            'is_valid': False,
            'error': (
                "This Stellar account does not exist on the network. "
                "Please fund your account first."
            ),
            'details': details,
        }
    details['account_exists'] = True

    # 3. Fetch all balances once
    balances = get_account_balances(public_key)
    if not balances:
        return {'is_valid': False, 'error': "Unable to fetch account balances.", 'details': details}

    xlm_balance = _Decimal('0')
    usdc_balance = _Decimal('0')

    for b in balances:
        if b['asset'] == 'XLM':
            xlm_balance = _Decimal(b['balance'])
        if b.get('asset') == 'USDC' and b.get('issuer') == settings.USDC_ISSUER:
            usdc_balance = _Decimal(b['balance'])
            details['has_trustline'] = True

    details['usdc_balance'] = str(usdc_balance)
    details['xlm_balance'] = str(xlm_balance)

    if not details['has_trustline']:
        return {
            'is_valid': False,
            'error': (
                "Your account does not have a USDC trustline. "
                "Please add one in your Stellar wallet before paying."
            ),
            'details': details,
        }

    # 4. USDC balance check
    if usdc_balance < _Decimal(str(required_amount_usdc)):
        return {
            'is_valid': False,
            'error': (
                f"Insufficient USDC balance. "
                f"You have {usdc_balance:.2f} USDC, "
                f"need {required_amount_usdc:.2f} USDC."
            ),
            'details': details,
        }

    # 5. XLM fee check (minimum 0.0001 XLM needed beyond base reserve)
    min_reqs = get_minimum_account_requirements()
    if xlm_balance < _Decimal(str(min_reqs['transaction_fee'] + min_reqs['base_reserve'])):
        return {
            'is_valid': False,
            'error': (
                f"Insufficient XLM for transaction fees. "
                f"You have {xlm_balance:.4f} XLM; need at least "
                f"{min_reqs['transaction_fee'] + min_reqs['base_reserve']:.4f} XLM."
            ),
            'details': details,
        }

    return {'is_valid': True, 'error': None, 'details': details}


# ═══════════════════════════════════════════════════════════════════════════════
# XDR generation & submission — for non-custodial (wallet) path
# ═══════════════════════════════════════════════════════════════════════════════

def generate_payment_xdr(
    from_account: str,
    to_account: str,
    amount_usdc: str,
    memo_text: str = "LIPASTELLAR",
) -> str:
    """
    Build an *unsigned* USDC payment transaction and return its XDR envelope.

    The XDR can be pasted into Stellar Laboratory (or any wallet) for signing.
    The from_account must already exist on the network.

    Args:
        from_account: Sender's public key (G-address).
        to_account:   Recipient's public key (G-address).
        amount_usdc:  Payment amount as a string (e.g. "10.0000000").
        memo_text:    Memo text (max 28 bytes).

    Returns:
        Base64-encoded XDR string of the unsigned transaction envelope.
    """
    server = get_server()
    account = server.load_account(from_account)
    usdc_asset = Asset('USDC', settings.USDC_ISSUER)

    tx = (
        TransactionBuilder(
            source_account=account,
            network_passphrase=get_network_passphrase(),
            base_fee=100,
        )
        .append_payment_op(
            destination=to_account,
            asset=usdc_asset,
            amount=str(amount_usdc),
        )
        .add_text_memo(memo_text[:28])
        .set_timeout(300)   # 5 minutes for the user to sign
        .build()
    )
    return tx.to_xdr()


def submit_signed_transaction(signed_xdr: str) -> str:
    """
    Submit a signed XDR transaction to the Stellar network.

    Args:
        signed_xdr: Base64-encoded XDR of a signed transaction envelope.

    Returns:
        Transaction hash (hex string).

    Raises:
        BadRequestError: If Horizon rejects the transaction.
        Exception:       For other network errors.
    """
    from stellar_sdk import TransactionEnvelope

    server = get_server()
    envelope = TransactionEnvelope.from_xdr(signed_xdr, network_passphrase=get_network_passphrase())
    response = server.submit_transaction(envelope)
    return response['hash']


# ═══════════════════════════════════════════════════════════════════════════════
# Master account — treasury / pooled payments
# ═══════════════════════════════════════════════════════════════════════════════

def get_master_customer_keypair() -> "Keypair":
    """
    Return the Keypair for the shared master customer account.
    The secret is read from MASTER_CUSTOMER_SECRET env var.

    Raises:
        ValueError: If the env var is not set.
    """
    secret = os.getenv('MASTER_CUSTOMER_SECRET')
    if not secret:
        raise ValueError(
            "MASTER_CUSTOMER_SECRET is not set in the environment. "
            "Please generate a funded Stellar testnet account and add its "
            "secret key to your .env file."
        )
    return Keypair.from_secret(secret)


def get_master_balance(issuer: str | None = None) -> "Decimal":
    """
    Return the current USDC balance of the master customer account.

    Args:
        issuer: USDC issuer address. Defaults to settings.USDC_ISSUER.

    Returns:
        Decimal USDC balance. Returns 0 if the account has no USDC trustline.

    Raises:
        ValueError: If MASTER_CUSTOMER_SECRET is not configured.
        Exception:  On Horizon network errors.
    """
    from decimal import Decimal as _Decimal
    if issuer is None:
        issuer = settings.USDC_ISSUER

    keypair = get_master_customer_keypair()
    balances = get_account_balances(keypair.public_key)
    if not balances:
        return _Decimal('0')

    for b in balances:
        if b.get('asset') == 'USDC' and b.get('issuer') == issuer:
            return _Decimal(b['balance'])
    return _Decimal('0')
