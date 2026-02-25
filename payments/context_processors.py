from django.conf import settings


def stellar_settings(request):
    return {
        'TESTNET_EXPLORER_URL': settings.TESTNET_EXPLORER_URL,
    }


def session_customer(request):
    """
    Expose the current session-based app customer to every template.
    Used by the navbar to show/hide customer account links.
    """
    from payments.models import Customer
    phone = request.session.get('customer_phone')
    customer = None
    if phone:
        customer = Customer.objects.filter(
            phone_number=phone, customer_type='app', is_active=True
        ).first()
    return {'session_customer': customer}

