from django.urls import path

from portal import views

urlpatterns = [
    path('healthz', views.healthz),
    path('sellers/ensure', views.ensure_seller),
    path('listings', views.listings),
    path('listings/<int:listing_id>', views.listing_detail),
    path('sales', views.sales),
]
