# scores_app/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.nhl_scores_view, name='nhl_scores_home'),
]
