"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import include, path

from workspaces import views as workspace_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/signup/', workspace_views.teacher_signup, name='signup'),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', include('workspaces.urls')),
]
