from django.urls import path

from . import views

urlpatterns = [
    path('', views.workspace_list, name='workspace_list'),
    path('new/', views.workspace_create, name='workspace_create'),
    path('<int:pk>/', views.workspace_detail, name='workspace_detail'),
    path('join/', views.student_join, name='student_join'),
    path('chat/', views.student_chat, name='student_chat'),
    path('chat/send/', views.send_message, name='send_message'),
]
