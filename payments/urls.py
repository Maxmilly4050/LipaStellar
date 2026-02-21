from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('register/', views.register, name='merchant_register'),
    path('login/', auth_views.LoginView.as_view(template_name='payments/merchant_login.html'), name='merchant_login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='index'), name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('history/', views.transaction_history, name='transaction_history'),
    path('pay/', views.payment_form, name='payment_form'),
    path('success/<str:tx_hash>/', views.payment_success, name='payment_success'),
    path('api/balance/', views.api_balance, name='api_balance'),
]
