from django.urls import path

from ops import views

# The edge strips the /oe prefix, so these are the paths behind
# http://<host>/oe/ — e.g. /oe/api/containers arrives here as api/containers.
urlpatterns = [
    path('', views.index),
    path('healthz', views.healthz),
    path('app.css', views.app_css),
    path('pkce.js', views.pkce_js),
    path('app.js', views.app_js),
    path('api/containers', views.containers),
]
