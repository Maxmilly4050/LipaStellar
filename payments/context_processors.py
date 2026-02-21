from django.conf import settings

def stellar_settings(request):
    return {
        'TESTNET_EXPLORER_URL': settings.TESTNET_EXPLORER_URL,
    }
