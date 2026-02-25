from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from . import views_api
from . import views_deposit
from . import views_withdrawal

urlpatterns = [
    # ── Public / auth ────────────────────────────────────────────────────────
    path('', views.index, name='index'),
    path('register/', views.register, name='merchant_register'),
    path('login/', views.merchant_login, name='merchant_login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='index'), name='logout'),

    # ── Merchant dashboard ───────────────────────────────────────────────────
    path('dashboard/', views.dashboard, name='dashboard'),
    path('history/', views.transaction_history, name='transaction_history'),

    # ── Legacy single-step payment (XLM path) ───────────────────────────────
    path('pay/', views.payment_form, name='payment_form'),

    # ── Dual-path customer payment flow ─────────────────────────────────────
    path('pay/method/', views.payment_method, name='payment_method'),
    path('pay/wallet/', views.payment_wallet, name='payment_wallet'),
    path('pay/wallet/sign/', views.payment_sign, name='payment_sign'),
    path('pay/phone/', views.payment_phone, name='payment_phone'),

    path('success/<str:tx_hash>/', views.payment_success, name='payment_success'),

    # ── AJAX / JSON API ──────────────────────────────────────────────────────
    path('api/balance/', views.api_balance, name='api_balance'),
    path('api/validate-stellar/', views_api.api_validate_stellar_account, name='api_validate_stellar'),
    path('api/wallet-balance/', views_api.api_check_wallet_balance, name='api_check_balance'),
    path('api/submit-tx/', views_api.api_submit_transaction, name='api_submit_tx'),

    # ── Phase 2: Customer auth (phone-based session) ─────────────────────────
    path('account/login/', views_deposit.customer_login, name='customer_login'),
    path('account/logout/', views_deposit.customer_logout, name='customer_logout'),
    path('account/dashboard/', views_deposit.customer_dashboard, name='customer_dashboard'),

    # ── Phase 2: Deposits ────────────────────────────────────────────────────
    path('account/deposit/', views_deposit.deposit_request, name='deposit_request'),
    path('account/deposit/history/', views_deposit.deposit_history, name='deposit_history'),
    path('account/deposit/<int:deposit_id>/status/', views_deposit.deposit_status, name='deposit_status'),
    path('account/deposit/<int:deposit_id>/pending/', views_deposit.deposit_pending, name='deposit_pending'),
    path('account/deposit/webhook/', views_deposit.deposit_webhook, name='deposit_webhook'),

    # ── Phase 2: Withdrawals ─────────────────────────────────────────────────
    path('account/withdraw/', views_withdrawal.withdrawal_request, name='withdrawal_request'),
    path('account/withdraw/history/', views_withdrawal.withdrawal_history, name='withdrawal_history'),
    path('account/withdraw/pending/', views_withdrawal.withdrawal_pending_list, name='withdrawal_pending_list'),
    path('account/withdraw/<int:withdrawal_id>/approve/', views_withdrawal.withdrawal_approve, name='withdrawal_approve'),

    # ── Phase 2: Treasury (staff only) ──────────────────────────────────────
    path('treasury/', views_withdrawal.treasury_dashboard, name='treasury_dashboard'),

    # ── Merchant withdrawals (separate from customer withdrawals) ────────────
    path('merchant/withdraw/', views.merchant_withdraw_request, name='merchant_withdraw_request'),
    path('merchant/withdraw/history/', views.merchant_withdraw_history, name='merchant_withdraw_history'),
]
