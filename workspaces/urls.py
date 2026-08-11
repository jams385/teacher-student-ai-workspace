from django.urls import path

from . import views

urlpatterns = [
    path('', views.workspace_list, name='workspace_list'),
    path('new/', views.workspace_create, name='workspace_create'),
]
