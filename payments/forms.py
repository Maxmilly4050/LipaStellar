from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Merchant

class MerchantRegistrationForm(UserCreationForm):
    business_name = forms.CharField(max_length=200)
    phone_number = forms.CharField(max_length=15)
    username = forms.CharField(max_length=50, help_text="e.g., @mama_cafe")
    
    class Meta:
        model = User
        fields = ('username', 'business_name', 'phone_number')
    
    def clean_username(self):
        username = self.cleaned_data['username']
        if Merchant.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username

class CustomerPaymentForm(forms.Form):
    merchant_username = forms.CharField(max_length=50, label="Merchant Username (e.g., @mama_cafe)")
    amount_tzs = forms.DecimalField(max_digits=12, decimal_places=2, label="Amount (TZS)")
    customer_phone = forms.CharField(max_length=15, label="Your Phone Number")
    
    def clean_merchant_username(self):
        username = self.cleaned_data['merchant_username']
        if not Merchant.objects.filter(username=username).exists():
            raise forms.ValidationError("Merchant not found.")
        return username
