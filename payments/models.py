import secrets
import string

from django.contrib.auth.models import User
from django.db import models
from django.conf import settings


# ─── Customer ──────────────────────────────────────────────────────────────────

CUSTOMER_TYPES = (
    ('app', 'App-based (Pooled Account)'),
    ('wallet', 'Self-custodied (Own Wallet)'),
)


class Customer(models.Model):
    """
    Represents a paying customer.

    Path A (wallet): Customers with their own Stellar wallets.
      Identified by stellar_public_key.  They sign transactions themselves.

    Path B (app): Customers without wallets, identified by phone number.
      Payments are sent from the shared master account using their memo code.
      Balance is tracked off-chain in balance_usdc.
    """
    customer_type = models.CharField(max_length=10, choices=CUSTOMER_TYPES)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    # Optional link to Django User (for deposit/withdrawal auth)
    user = models.OneToOneField(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='customer'
    )

    # Path B: App-based (phone + memo)
    phone_number = models.CharField(max_length=15, unique=True, null=True, blank=True)
    stellar_memo = models.CharField(max_length=28, unique=True, null=True, blank=True)
    balance_usdc = models.DecimalField(max_digits=12, decimal_places=7, default=0)

    # Path A: Wallet-based (public key only, no secret stored)
    stellar_public_key = models.CharField(max_length=56, unique=True, null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(customer_type='app', phone_number__isnull=False) |
                    models.Q(customer_type='wallet', stellar_public_key__isnull=False)
                ),
                name='customer_type_field_consistency',
            )
        ]

    def __str__(self):
        if self.customer_type == 'app':
            return f"App: {self.phone_number} (Memo: {self.stellar_memo})"
        return f"Wallet: {self.stellar_public_key[:8]}…"

    def get_available_balance(self):
        """For app customers only — returns off-chain USDC balance."""
        if self.customer_type == 'app':
            return self.balance_usdc
        return None

    @staticmethod
    def generate_memo() -> str:
        """Generate a unique 8-char alphanumeric memo for app customers."""
        alphabet = string.ascii_uppercase + string.digits
        while True:
            memo = ''.join(secrets.choice(alphabet) for _ in range(8))
            if not Customer.objects.filter(stellar_memo=memo).exists():
                return memo


# ─── Merchant ──────────────────────────────────────────────────────────────────

class Merchant(models.Model):
    """
    Custodial merchant profile.

    Public identity data (username, business name, phone) lives on the Stellar
    ledger as ManageData entries.  This model stores only:
      - The encrypted Stellar secret key (useless without SECRET_KEY env var)
      - Read-through cache fields so the app can work even when Horizon is slow
      - A timestamp tracking the last successful sync from the ledger
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    stellar_public_key = models.CharField(max_length=56, unique=True)
    stellar_secret_encrypted = models.TextField()
    last_synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # Local cache of on-chain data — populated on registration and refreshed on
    # every dashboard load.  Never used as a source-of-truth; Stellar ledger wins.
    cached_username = models.CharField(max_length=50, blank=True, null=True)
    cached_business_name = models.CharField(max_length=200, blank=True, null=True)

    def __str__(self):
        return self.cached_username or self.user.username

    def get_explorer_url(self):
        """Return account payments tab URL on Testnet Explorer."""
        return f"{settings.TESTNET_EXPLORER_URL}/account/{self.stellar_public_key}?filter=payments"

    def get_explorer_account_url(self):
        """Return account overview URL on Testnet Explorer."""
        return f"{settings.TESTNET_EXPLORER_URL}/account/{self.stellar_public_key}"

    def set_secret_key(self, raw_secret: str) -> None:
        """Encrypt and store the Stellar secret key."""
        from .security import encrypt_secret
        self.stellar_secret_encrypted = encrypt_secret(raw_secret)

    def get_secret_key(self) -> str:
        """
        Decrypt and return the Stellar secret key.

        Use this ONLY immediately before signing a transaction.
        Never log the returned value.
        """
        from .security import decrypt_secret
        return decrypt_secret(self.stellar_secret_encrypted)

class Transaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    DIRECTION_CHOICES = [
        ('inbound', 'Inbound'),
        ('outbound', 'Outbound'),
    ]
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE)
    transaction_hash = models.CharField(max_length=64)
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=2)
    # amount_xlm: kept for backwards-compat with existing XLM-path transactions
    amount_xlm = models.DecimalField(max_digits=12, decimal_places=7, null=True, blank=True)
    # amount_usdc: populated by the new dual-path (wallet / phone) flows
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=7, null=True, blank=True)
    customer_phone = models.CharField(max_length=15, blank=True, default='')
    memo = models.CharField(max_length=28)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='completed')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='inbound')
    created_at = models.DateTimeField(auto_now_add=True)

    # Dual-path customer tracking
    customer_app = models.ForeignKey(
        Customer,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='app_transactions',
        limit_choices_to={'customer_type': 'app'},
    )
    customer_wallet_public_key = models.CharField(max_length=56, null=True, blank=True)

    @property
    def payment_path(self):
        if self.customer_app:
            return 'app'
        elif self.customer_wallet_public_key:
            return 'wallet'
        return 'legacy'

    @property
    def currency(self):
        """Returns 'USDC' or 'XLM' based on which amount field is populated."""
        if self.amount_usdc is not None:
            return 'USDC'
        return 'XLM'

    @property
    def crypto_amount(self):
        """Returns the crypto amount regardless of currency type."""
        if self.amount_usdc is not None:
            return self.amount_usdc
        return self.amount_xlm

    @property
    def display_amount(self):
        """Return the most meaningful amount for display."""
        if self.amount_usdc is not None:
            return f"{self.amount_usdc:.7f} USDC"
        if self.amount_xlm is not None:
            return f"{self.amount_xlm:.7f} XLM"
        return "—"

    def __str__(self):
        return f"{self.transaction_hash[:8]} - {self.amount_tzs} TZS"

    def get_explorer_url(self):
        """Return transaction detail URL on Testnet Explorer."""
        return f"{settings.TESTNET_EXPLORER_URL}/tx/{self.transaction_hash}"


# ─── Treasury / Deposit / Withdrawal ──────────────────────────────────────────

class Deposit(models.Model):
    """Track customer USDC deposits (funded via mobile money)."""
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        limit_choices_to={'customer_type': 'app'},
        related_name='deposits',
    )
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=2)
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=7)
    payment_method = models.CharField(max_length=20, default='mobile_money')
    provider_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='deposits_created',
    )

    def __str__(self):
        return f"{self.customer.phone_number} — {self.amount_tzs} TZS ({self.status})"


class Withdrawal(models.Model):
    """Track customer withdrawal requests (paid out via mobile money)."""
    STATUS_CHOICES = (
        ('requested', 'Requested'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE,
        limit_choices_to={'customer_type': 'app'},
        related_name='withdrawals',
    )
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=2)
    amount_usdc = models.DecimalField(max_digits=12, decimal_places=7)
    payout_method = models.CharField(max_length=20, default='mobile_money')
    provider_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='requested')

    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    approved_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='withdrawals_approved',
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.customer.phone_number} — {self.amount_tzs} TZS ({self.status})"


class TreasuryLog(models.Model):
    """Immutable audit log for all treasury operations."""
    EVENT_TYPES = (
        ('rebalance', 'Rebalancing'),
        ('reconciliation', 'Reconciliation'),
        ('deposit', 'Deposit Processed'),
        ('withdrawal', 'Withdrawal Processed'),
        ('alert', 'Alert Triggered'),
        ('manual_adjustment', 'Manual Adjustment'),
    )

    event_type = models.CharField(max_length=20, choices=EVENT_TYPES)
    master_balance_before = models.DecimalField(max_digits=20, decimal_places=7)
    master_balance_after = models.DecimalField(max_digits=20, decimal_places=7)
    db_total_before = models.DecimalField(max_digits=20, decimal_places=7, null=True, blank=True)
    db_total_after = models.DecimalField(max_digits=20, decimal_places=7, null=True, blank=True)
    discrepancy = models.DecimalField(max_digits=20, decimal_places=7, null=True, blank=True)

    deposit = models.ForeignKey(Deposit, null=True, blank=True, on_delete=models.SET_NULL)
    withdrawal = models.ForeignKey(Withdrawal, null=True, blank=True, on_delete=models.SET_NULL)

    notes = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='treasury_logs',
    )

    def __str__(self):
        return f"{self.get_event_type_display()} @ {self.created_at:%Y-%m-%d %H:%M}"


class LiquidityAlert(models.Model):
    """Track low master-account balance alerts."""
    threshold = models.DecimalField(max_digits=20, decimal_places=7)
    current_balance = models.DecimalField(max_digits=20, decimal_places=7)
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        status = "resolved" if self.resolved else "open"
        return f"Alert {status}: {self.current_balance} USDC @ {self.created_at:%Y-%m-%d}"


class MerchantWithdrawal(models.Model):
    """
    Merchant requests cash-out of their earned Stellar balance (XLM or USDC).
    Staff approves and disburses via mobile money / bank transfer.
    """
    STATUS_CHOICES = (
        ('requested', 'Requested'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )
    CURRENCY_CHOICES = (
        ('XLM', 'XLM'),
        ('USDC', 'USDC'),
    )

    merchant = models.ForeignKey(
        Merchant, on_delete=models.CASCADE, related_name='withdrawals'
    )
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=2)
    amount_stellar = models.DecimalField(max_digits=16, decimal_places=7)
    currency = models.CharField(max_length=4, choices=CURRENCY_CHOICES, default='USDC')
    payout_phone = models.CharField(max_length=20, blank=True)
    payout_method = models.CharField(max_length=20, default='mobile_money')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='requested')
    notes = models.TextField(blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        'auth.User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='merchant_withdrawals_approved',
    )

    def __str__(self):
        return f"{self.merchant.cached_username} — {self.amount_tzs} TZS ({self.currency}, {self.status})"
