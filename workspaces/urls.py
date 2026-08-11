from django.urls import path

from . import views

urlpatterns = [
    path('', views.workspace_list, name='workspace_list'),
    path('new/', views.workspace_create, name='workspace_create'),
    path('join/', views.student_join, name='student_join'),
    path('chat/', views.student_chat, name='student_chat'),
]
