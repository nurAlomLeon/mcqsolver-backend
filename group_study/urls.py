from django.urls import path

from .views import (
    GroupMessageListCreateView,
    GroupMessageNotificationView,
    GroupMessageReadView,
    GroupStudyQuizAddQuestionsView,
    GroupStudyQuizAttemptResultView,
    GroupStudyQuizAttemptStartView,
    GroupStudyQuizAttemptSubmitView,
    GroupStudyQuizDetailView,
    GroupStudyQuizLeaderboardView,
    GroupStudyQuizListCreateView,
    GroupStudyQuizPublishView,
    StudyGroupDetailView,
    StudyGroupLeaderboardView,
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
    path('groups/<int:group_id>/messages/notifications/', GroupMessageNotificationView.as_view(), name='group-message-notifications'),
    path('groups/<int:group_id>/leaderboard/', StudyGroupLeaderboardView.as_view(), name='group-leaderboard'),
    path('groups/<int:group_id>/quizzes/', GroupStudyQuizListCreateView.as_view(), name='group-quiz-list-create'),
    path('quizzes/<int:quiz_id>/', GroupStudyQuizDetailView.as_view(), name='group-quiz-detail'),
    path('quizzes/<int:quiz_id>/leaderboard/', GroupStudyQuizLeaderboardView.as_view(), name='group-quiz-leaderboard'),
    path('quizzes/<int:quiz_id>/add-questions/', GroupStudyQuizAddQuestionsView.as_view(), name='group-quiz-add-questions'),
    path('quizzes/<int:quiz_id>/publish/', GroupStudyQuizPublishView.as_view(), name='group-quiz-publish'),
    path('quizzes/<int:quiz_id>/attempts/start/', GroupStudyQuizAttemptStartView.as_view(), name='group-quiz-attempt-start'),
    path('attempts/<int:attempt_id>/submit/', GroupStudyQuizAttemptSubmitView.as_view(), name='group-quiz-attempt-submit'),
    path('attempts/<int:attempt_id>/result/', GroupStudyQuizAttemptResultView.as_view(), name='group-quiz-attempt-result'),
]
