from django.urls import path

from . import views

urlpatterns = [
    path('', views.workspace_list, name='workspace_list'),
    path('new/', views.workspace_create, name='workspace_create'),
    path('<int:pk>/', views.workspace_detail, name='workspace_detail'),
    path('<int:pk>/lecture/outline/', views.generate_lecture_outline, name='generate_lecture_outline'),
    path('<int:pk>/materials/<int:material_pk>/delete/', views.material_delete, name='material_delete'),
    path('<int:pk>/dashboard/', views.workspace_dashboard, name='workspace_dashboard'),
    path('<int:pk>/dashboard/<int:session_pk>/', views.session_transcript, name='session_transcript'),
    path('<int:pk>/dashboard/flags/<int:flag_pk>/reviewed/', views.flag_mark_reviewed, name='flag_mark_reviewed'),
    path('join/', views.student_join, name='student_join'),
    path('chat/', views.student_chat, name='student_chat'),
    path('chat/send/', views.send_message, name='send_message'),
]
