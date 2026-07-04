from django.urls import path

from checkout import views

urlpatterns = [
    path('healthz', views.healthz),
    path('checkout', views.checkout),
]
