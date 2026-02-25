import os
from pathlib import Path
from dotenv import load_dotenv
import dj_database_url

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-fallback-key')
DEBUG = os.getenv('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = [
    'localhost',
    '127.0.0.1',
    '.vercel.app',
    '.now.sh',
    '*',  # tighten this after confirming deployment
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'payments',
    'crispy_forms',
    'crispy_bootstrap5',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lipastellar.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'payments.context_processors.stellar_settings',
                'payments.context_processors.session_customer',
            ],
        },
    },
]

WSGI_APPLICATION = 'lipastellar.wsgi.application'


_db_url = os.getenv('DATABASE_URL', 'sqlite:///db.sqlite3')
DATABASES = {
    'default': dj_database_url.config(
        default=_db_url,
        conn_max_age=600,
        # Only require SSL for PostgreSQL (not SQLite)
        ssl_require=_db_url.startswith('postgres'),
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles_build' / 'static'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"

# Auth
LOGIN_URL = 'merchant_login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'index'

# Stellar Settings
STELLAR_NETWORK = os.getenv('STELLAR_NETWORK', 'TESTNET')
STELLAR_HORIZON_URL = os.getenv('STELLAR_HORIZON_URL', 'https://horizon-testnet.stellar.org')
STELLAR_NETWORK_PASSPHRASE = os.getenv('STELLAR_NETWORK_PASSPHRASE', 'Test SDF Network ; September 2015')
USDC_ISSUER = os.getenv('USDC_ISSUER', 'GCSACNK7RMSZSYKJKGZYNNE23G4IWO6SOV2ESIEGQB5YKXOKGMEU2PLD')
FRIENDBOT_URL = os.getenv('FRIENDBOT_URL', 'https://friendbot.stellar.org')
TESTNET_EXPLORER_URL = os.getenv('TESTNET_EXPLORER_URL', 'https://stellar.expert/explorer/testnet')

# Master pooled customer account (Phase 2)
# Generate a funded testnet account and set MASTER_CUSTOMER_SECRET in .env
MASTER_CUSTOMER_SECRET = os.getenv('MASTER_CUSTOMER_SECRET', '')

# Mobile money provider: 'mock' (default/testnet) | future: 'vodacom' | 'airtel'
MOBILE_MONEY_PROVIDER = os.getenv('MOBILE_MONEY_PROVIDER', 'mock')

# Treasury liquidity alert thresholds (USDC)
LIQUIDITY_LOW_THRESHOLD = os.getenv('LIQUIDITY_LOW_THRESHOLD', '100')
LIQUIDITY_CRITICAL_THRESHOLD = os.getenv('LIQUIDITY_CRITICAL_THRESHOLD', '50')

