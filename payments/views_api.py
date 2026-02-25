"""
AJAX / JSON API endpoints for the dual-path customer onboarding system.
"""
import logging
from decimal import Decimal

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt

from . import stellar_utils

logger = logging.getLogger(__name__)


@require_POST
def api_validate_stellar_account(request):
    """
    Validate a Stellar public key and check readiness for payment.

    POST params:
        public_key  - Stellar G-address
        amount_usdc - (optional) required USDC amount for balance check

    Returns JSON:
        {
          "valid": bool,
          "error": str | null,
          "details": {
              "account_exists": bool,
              "has_trustline": bool,
              "usdc_balance": str,
              "xlm_balance": str,
          }
        }
    """
    public_key = request.POST.get('public_key', '').strip()
    amount_usdc_raw = request.POST.get('amount_usdc', '0').strip()

    # Format check first (cheap)
    fmt_ok, fmt_err = stellar_utils.validate_stellar_public_key(public_key)
    if not fmt_ok:
        return JsonResponse({'valid': False, 'error': fmt_err, 'details': {}})

    try:
        amount_usdc = Decimal(amount_usdc_raw)
    except Exception:
        amount_usdc = Decimal('0')

    result = stellar_utils.validate_account_for_payment(public_key, amount_usdc)
    return JsonResponse({
        'valid': result['is_valid'],
        'error': result['error'],
        'details': result['details'],
    })


@require_GET
def api_check_wallet_balance(request):
    """
    Return live USDC and XLM balances for a Stellar public key.

    GET params:
        public_key - Stellar G-address

    Returns JSON:
        {
          "usdc_balance": str,
          "xlm_balance": str,
          "has_trustline": bool,
          "error": str | null
        }
    """
    public_key = request.GET.get('public_key', '').strip()

    fmt_ok, fmt_err = stellar_utils.validate_stellar_public_key(public_key)
    if not fmt_ok:
        return JsonResponse({'error': fmt_err}, status=400)

    if not stellar_utils.account_exists(public_key):
        return JsonResponse({
            'error': 'Account not found on Stellar network.',
            'usdc_balance': '0',
            'xlm_balance': '0',
            'has_trustline': False,
        }, status=404)

    balances = stellar_utils.get_account_balances(public_key)
    if not balances:
        return JsonResponse({'error': 'Unable to fetch balances.'}, status=500)

    xlm_balance = '0'
    usdc_balance = '0'
    has_trustline = False

    for b in balances:
        if b['asset'] == 'XLM':
            xlm_balance = b['balance']
        if b.get('asset') == 'USDC':
            usdc_balance = b['balance']
            has_trustline = True

    return JsonResponse({
        'usdc_balance': usdc_balance,
        'xlm_balance': xlm_balance,
        'has_trustline': has_trustline,
        'error': None,
    })


@require_POST
def api_submit_transaction(request):
    """
    Submit a signed Stellar XDR transaction from a wallet customer.

    POST params:
        signed_xdr - Base64-encoded signed XDR envelope

    Returns JSON:
        {"tx_hash": str}  on success
        {"error": str}    on failure
    """
    signed_xdr = request.POST.get('signed_xdr', '').strip()
    if not signed_xdr:
        return JsonResponse({'error': 'signed_xdr is required.'}, status=400)

    try:
        tx_hash = stellar_utils.submit_signed_transaction(signed_xdr)
        return JsonResponse({'tx_hash': tx_hash})
    except Exception as exc:
        logger.error("api_submit_transaction error: %s", exc)
        return JsonResponse({'error': str(exc)}, status=400)
