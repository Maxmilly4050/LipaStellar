"""
Mobile money provider abstraction for LipaStellar.

Real providers (Vodacom M-Pesa Tanzania, Airtel Money, etc.) plug in by
subclassing MobileMoneyProvider.  In development / testnet the MockProvider
is used — it simulates instant success so the flow can be tested end-to-end
without a live carrier API.

To add a real provider:
1. Create a subclass of MobileMoneyProvider.
2. Implement all abstract methods.
3. Register it in MOBILE_MONEY_PROVIDER_CLASS in settings.py.
"""

import logging
import os
import uuid
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class MobileMoneyProvider(ABC):
    """Abstract interface for mobile money integrations."""

    @abstractmethod
    def request_payment(self, phone_number: str, amount_tzs, reference: str) -> dict:
        """
        Initiate a payment request from the customer's mobile wallet.

        Args:
            phone_number: Customer's MSISDN in international format (+255…).
            amount_tzs:   Amount to charge in Tanzanian Shilling.
            reference:    Unique reference for this deposit (e.g. "DEP42").

        Returns:
            {
              "success": bool,
              "reference": str,   # Provider's transaction reference
              "error": str | None
            }
        """

    @abstractmethod
    def verify_payment(self, provider_reference: str) -> dict:
        """
        Check the current status of a payment.

        Returns:
            {
              "success": bool,
              "status": "pending" | "completed" | "failed",
              "error": str | None
            }
        """

    @abstractmethod
    def send_payout(self, phone_number: str, amount_tzs, reference: str) -> dict:
        """
        Push money to a customer's mobile wallet (withdrawal).

        Returns:
            {
              "success": bool,
              "reference": str,
              "error": str | None
            }
        """

    @abstractmethod
    def handle_webhook(self, request) -> dict:
        """
        Parse and validate an inbound webhook from the provider.

        Returns:
            {
              "success": bool,
              "reference": str,   # Provider reference matching a Deposit
              "status": str,
              "error": str | None
            }
        """


class MockProvider(MobileMoneyProvider):
    """
    Simulated mobile money provider for testnet / CI.

    Every payment request and payout succeeds instantly.
    Webhooks are not used — the deposit webhook view is called directly
    after a successful request_payment.
    """

    def request_payment(self, phone_number, amount_tzs, reference):
        ref = f"MOCK-{uuid.uuid4().hex[:8].upper()}"
        logger.info("[MockProvider] request_payment %s %s TZS → ref=%s", phone_number, amount_tzs, ref)
        return {'success': True, 'reference': ref, 'error': None}

    def verify_payment(self, provider_reference):
        logger.info("[MockProvider] verify_payment %s → completed", provider_reference)
        return {'success': True, 'status': 'completed', 'error': None}

    def send_payout(self, phone_number, amount_tzs, reference):
        ref = f"MOCK-OUT-{uuid.uuid4().hex[:8].upper()}"
        logger.info("[MockProvider] send_payout %s %s TZS → ref=%s", phone_number, amount_tzs, ref)
        return {'success': True, 'reference': ref, 'error': None}

    def handle_webhook(self, request):
        # MockProvider does not use webhooks — deposits are completed synchronously.
        return {'success': False, 'reference': '', 'status': 'unknown', 'error': 'MockProvider has no webhooks.'}


def get_mobile_money_provider() -> MobileMoneyProvider:
    """
    Return the configured mobile money provider.

    Set MOBILE_MONEY_PROVIDER=mock|vodacom|airtel in .env.
    Defaults to MockProvider if not set or in DEBUG mode.
    """
    from django.conf import settings

    provider_name = getattr(settings, 'MOBILE_MONEY_PROVIDER', 'mock').lower()

    if provider_name == 'mock' or settings.DEBUG:
        return MockProvider()

    # Future: load real providers from a registry
    raise NotImplementedError(
        f"Mobile money provider '{provider_name}' is not yet implemented. "
        "Use MOBILE_MONEY_PROVIDER=mock for testnet development."
    )
