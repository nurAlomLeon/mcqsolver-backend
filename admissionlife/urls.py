from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminCategoryViewSet,
    AdminLabelViewSet,
    AdminQuestionViewSet,
    BatchLeaderboardView,
    BatchViewSet,
    CategoryViewSet,
    EnrollmentCheckView,
    ExamAttemptResultView,
    ExamAttemptStartView,
    ExamAttemptSubmitView,
    ExamLeaderboardView,
    ExamViewSet,
    PaymentViewSet,
    PracticeQuizAttemptResultView,
    PracticeQuizAttemptStartView,
    PracticeQuizAttemptSubmitView,
    PracticeQuizView,
    QuestionReportView,
    QuestionViewSet,
    SavedQuestionViewSet,
)

router = DefaultRouter()
router.register(r'batches', BatchViewSet, basename='batch')
router.register(r'payments', PaymentViewSet, basename='payment')
router.register(r'exams', ExamViewSet, basename='exam')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'questions', QuestionViewSet, basename='question')
router.register(r'saved-questions', SavedQuestionViewSet, basename='saved-question')

admin_router = DefaultRouter()
admin_router.register(r'admin/questions', AdminQuestionViewSet, basename='admin-question')
admin_router.register(r'admin/categories', AdminCategoryViewSet, basename='admin-category')
admin_router.register(r'admin/labels', AdminLabelViewSet, basename='admin-label')

urlpatterns = [
    # Router-generated URLs (CRUD + custom actions for ViewSets)
    path('', include(router.urls)),

    # Admin router URLs
    path('', include(admin_router.urls)),

    # Batch leaderboard
    path(
        'batches/<int:batch_id>/leaderboard/',
        BatchLeaderboardView.as_view(),
        name='batch-leaderboard',
    ),

    # Enrollment check
    path(
        'enrollments/check/<int:batch_id>/',
        EnrollmentCheckView.as_view(),
        name='enrollment-check',
    ),

    # Exam leaderboard
    path(
        'exams/<int:exam_id>/leaderboard/',
        ExamLeaderboardView.as_view(),
        name='exam-leaderboard',
    ),

    # Exam attempt endpoints
    path(
        'exam-attempts/<int:exam_id>/start/',
        ExamAttemptStartView.as_view(),
        name='exam-attempt-start',
    ),
    path(
        'exam-attempts/<int:attempt_id>/submit/',
        ExamAttemptSubmitView.as_view(),
        name='exam-attempt-submit',
    ),
    path(
        'exam-attempts/<int:attempt_id>/result/',
        ExamAttemptResultView.as_view(),
        name='exam-attempt-result',
    ),

    # Question report endpoint
    path(
        'question-reports/',
        QuestionReportView.as_view(),
        name='question-report',
    ),

    # Practice quiz endpoints
    path(
        'practice-quizzes/',
        PracticeQuizView.as_view(),
        name='practice-quiz',
    ),
    path(
        'practice-quizzes/<int:quiz_id>/start/',
        PracticeQuizAttemptStartView.as_view(),
        name='practice-quiz-attempt-start',
    ),
    path(
        'practice-quizzes/<int:attempt_id>/submit/',
        PracticeQuizAttemptSubmitView.as_view(),
        name='practice-quiz-attempt-submit',
    ),
    path(
        'practice-quizzes/<int:attempt_id>/result/',
        PracticeQuizAttemptResultView.as_view(),
        name='practice-quiz-attempt-result',
    ),
]
