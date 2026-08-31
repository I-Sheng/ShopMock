from django.urls import path

from people import views

# The edge strips the /hr prefix, so these are the paths behind
# http://<host>/hr/ — e.g. /hr/api/employees arrives here as api/employees.
urlpatterns = [
    path('', views.index),
    path('healthz', views.healthz),
    path('app.css', views.app_css),
    path('pkce.js', views.pkce_js),
    path('app.js', views.app_js),
    path('api/overview', views.overview),
    path('api/employees', views.employees),
    path('api/leave', views.leave),
]
