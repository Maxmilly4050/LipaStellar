from django.contrib.auth.models import User
from django.db import models
from django.conf import settings


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
        """Return account URL on Testnet Explorer."""
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
    amount_xlm = models.DecimalField(max_digits=12, decimal_places=7)
    customer_phone = models.CharField(max_length=15)
    memo = models.CharField(max_length=28)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='completed')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES, default='inbound')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_hash[:8]} - {self.amount_tzs} TZS"
    
    def get_explorer_url(self):
        """Return transaction URL on Testnet Explorer"""
        return f"{settings.TESTNET_EXPLORER_URL}/tx/{self.transaction_hash}"
