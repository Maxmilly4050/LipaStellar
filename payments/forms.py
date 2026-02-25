from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Merchant
from . import stellar_utils


class MerchantRegistrationForm(UserCreationForm):
    """
    Registration form — collects the three fields that will be stored
    on the Stellar ledger (business_name, phone_number, username).
    These are NOT persisted as model fields; only cached copies land in
    the database after the on-chain write succeeds.
    """
    business_name = forms.CharField(
        max_length=200,
        help_text="Your shop or business name (max 60 characters on-chain).",
    )
    phone_number = forms.CharField(
        max_length=15,
        help_text="Contact phone number.",
    )
    username = forms.CharField(
        max_length=50,
        help_text="Unique merchant username (e.g. @mama_cafe). "
                  "Stored on the Stellar blockchain.",
    )

    class Meta:
        model = User
        fields = ('username', 'business_name', 'phone_number')

    def clean_username(self):
        username = self.cleaned_data['username']
        # Check both the Django User table and the Merchant cached_username cache
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already taken.")
        if Merchant.objects.filter(cached_username=username).exists():
            raise forms.ValidationError(
                "This merchant username is already registered."
            )
        return username


class CustomerPaymentForm(forms.Form):
    """Legacy single-step payment form (XLM path — kept for backwards compat)."""
    merchant_username = forms.CharField(
        max_length=50,
        label="Merchant Username (e.g. @mama_cafe)",
    )
    amount_tzs = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        label="Amount (TZS)",
    )
    customer_phone = forms.CharField(max_length=15, label="Your Phone Number")

    def clean_merchant_username(self):
        username = self.cleaned_data['merchant_username']
        if not Merchant.objects.filter(cached_username=username).exists():
            raise forms.ValidationError("Merchant not found.")
        return username


# ─── Dual-path customer forms ─────────────────────────────────────────────────

class CustomerPaymentMethodForm(forms.Form):
    """Step 1: customer picks their payment path."""
    PAYMENT_METHOD_CHOICES = (
        ('wallet', '🔑 Stellar Wallet — USDC (own wallet, non-custodial)'),
        ('phone', '📱 Phone / App Balance — USDC (no wallet needed)'),
        ('xlm', '⚡ Direct XLM — classic Stellar payment'),
    )
    payment_method = forms.ChoiceField(
        choices=PAYMENT_METHOD_CHOICES,
        widget=forms.RadioSelect,
        initial='phone',
        label='',
    )


class WalletPaymentForm(forms.Form):
    """Step 2a: wallet customer — collect merchant, amount, public key."""
    merchant_username = forms.CharField(
        max_length=50,
        label="Merchant Username",
        widget=forms.TextInput(attrs={'placeholder': 'e.g. mama_cafe'}),
    )
    amount_tzs = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=1,
        label="Amount (TZS)",
        widget=forms.NumberInput(attrs={'placeholder': '5000'}),
    )
    stellar_public_key = forms.CharField(
        max_length=56,
        label="Your Stellar Public Key",
        help_text="Starts with 'G…' — found in your Stellar wallet.",
        widget=forms.TextInput(attrs={
            'placeholder': 'GXXXXXXXXXXXXXXXX…',
            'class': 'font-monospace',
        }),
    )

    def clean_merchant_username(self):
        username = self.cleaned_data['merchant_username']
        if not Merchant.objects.filter(cached_username=username).exists():
            raise forms.ValidationError("Merchant not found.")
        return username

    def clean_stellar_public_key(self):
        pk = self.cleaned_data['stellar_public_key'].strip()
        is_valid, error = stellar_utils.validate_stellar_public_key(pk)
        if not is_valid:
            raise forms.ValidationError(error)
        return pk


class PhonePaymentForm(forms.Form):
    """Step 2b: phone customer — collect merchant, amount, phone."""
    merchant_username = forms.CharField(
        max_length=50,
        label="Merchant Username",
        widget=forms.TextInput(attrs={'placeholder': 'e.g. mama_cafe'}),
    )
    amount_tzs = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=1,
        label="Amount (TZS)",
        widget=forms.NumberInput(attrs={'placeholder': '5000'}),
    )
    customer_phone = forms.CharField(
        max_length=15,
        label="Your Phone Number",
        help_text="e.g. +255712345678 — used to look up your app balance.",
        widget=forms.TextInput(attrs={'placeholder': '+255712345678'}),
    )

    def clean_merchant_username(self):
        username = self.cleaned_data['merchant_username']
        if not Merchant.objects.filter(cached_username=username).exists():
            raise forms.ValidationError("Merchant not found.")
        return username

    def clean_customer_phone(self):
        phone = self.cleaned_data['customer_phone'].strip()
        if not phone.startswith('+'):
            raise forms.ValidationError(
                "Please enter your phone number in international format (e.g. +255712345678)."
            )
        return phone
