from django.urls import path

from ledger import views

# The edge strips the /finance prefix, so these are the paths behind
# http://<host>/finance/ — e.g. /finance/api/overview arrives here as
# api/overview.
urlpatterns = [
    path('', views.index),
    path('healthz', views.healthz),
    path('app.css', views.app_css),
    path('pkce.js', views.pkce_js),
    path('app.js', views.app_js),
    path('api/overview', views.overview),
    path('api/transactions', views.transactions),
    path('api/payment-methods', views.payment_methods),
]
