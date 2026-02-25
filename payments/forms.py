from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Merchant


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
        # Look up via the cached_username field (source of truth for lookups)
        if not Merchant.objects.filter(cached_username=username).exists():
            raise forms.ValidationError("Merchant not found.")
        return username
