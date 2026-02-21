from django.contrib.auth.models import User
from django.db import models
from django.conf import settings
from cryptography.fernet import Fernet
import base64

class Merchant(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    business_name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=15)
    username = models.CharField(max_length=50, unique=True)  # e.g., @mama_cafe
    stellar_public_key = models.CharField(max_length=56, unique=True)
    stellar_secret_encrypted = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.username
    
    def get_explorer_url(self):
        """Return account URL on Testnet Explorer"""
        return f"{settings.TESTNET_EXPLORER_URL}/account/{self.stellar_public_key}"

    def set_secret_key(self, raw_secret):
        """Encrypt and store the secret key"""
        # We use a derived key from Django's SECRET_KEY
        key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].encode().ljust(32))
        f = Fernet(key)
        self.stellar_secret_encrypted = f.encrypt(raw_secret.encode()).decode()

    def get_secret_key(self):
        """Decrypt and return the secret key"""
        key = base64.urlsafe_b64encode(settings.SECRET_KEY[:32].encode().ljust(32))
        f = Fernet(key)
        return f.decrypt(self.stellar_secret_encrypted.encode()).decode()

class Transaction(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    merchant = models.ForeignKey(Merchant, on_delete=models.CASCADE)
    transaction_hash = models.CharField(max_length=64, unique=True)
    amount_tzs = models.DecimalField(max_digits=12, decimal_places=2)
    amount_xlm = models.DecimalField(max_digits=12, decimal_places=7)
    customer_phone = models.CharField(max_length=15)
    memo = models.CharField(max_length=28)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='completed')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_hash[:8]} - {self.amount_tzs} TZS"
    
    def get_explorer_url(self):
        """Return transaction URL on Testnet Explorer"""
        return f"{settings.TESTNET_EXPLORER_URL}/tx/{self.transaction_hash}"
