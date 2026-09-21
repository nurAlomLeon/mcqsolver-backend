from django.urls import path

from .views import (
    GroupMessageListCreateView,
    GroupMessageReadView,
    GroupStudyQuizAddQuestionsView,
    GroupStudyQuizAttemptResultView,
    GroupStudyQuizAttemptStartView,
    GroupStudyQuizAttemptSubmitView,
    GroupStudyQuizDetailView,
    GroupStudyQuizListCreateView,
    GroupStudyQuizPublishView,
    StudyGroupDetailView,
    StudyGroupListCreateView,
    StudyGroupMemberDetailView,
    StudyGroupMemberListView,
)


urlpatterns = [
    path('groups/', StudyGroupListCreateView.as_view(), name='group-list-create'),
    path('groups/<int:group_id>/', StudyGroupDetailView.as_view(), name='group-detail'),
    path('groups/<int:group_id>/members/', StudyGroupMemberListView.as_view(), name='group-member-list'),
    path('groups/<int:group_id>/members/<int:member_id>/', StudyGroupMemberDetailView.as_view(), name='group-member-detail'),
    path('groups/<int:group_id>/messages/', GroupMessageListCreateView.as_view(), name='group-message-list-create'),
    path('groups/<int:group_id>/messages/read/', GroupMessageReadView.as_view(), name='group-message-read'),
    path('groups/<int:group_id>/quizzes/', GroupStudyQuizListCreateView.as_view(), name='group-quiz-list-create'),
    path('quizzes/<int:quiz_id>/', GroupStudyQuizDetailView.as_view(), name='group-quiz-detail'),
    path('quizzes/<int:quiz_id>/add-questions/', GroupStudyQuizAddQuestionsView.as_view(), name='group-quiz-add-questions'),
    path('quizzes/<int:quiz_id>/publish/', GroupStudyQuizPublishView.as_view(), name='group-quiz-publish'),
    path('quizzes/<int:quiz_id>/attempts/start/', GroupStudyQuizAttemptStartView.as_view(), name='group-quiz-attempt-start'),
    path('attempts/<int:attempt_id>/submit/', GroupStudyQuizAttemptSubmitView.as_view(), name='group-quiz-attempt-submit'),
    path('attempts/<int:attempt_id>/result/', GroupStudyQuizAttemptResultView.as_view(), name='group-quiz-attempt-result'),
]
