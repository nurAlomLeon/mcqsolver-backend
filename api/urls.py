
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

# Create a router and register our viewsets with it.
router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')

router.register(r'questions', QuestionViewSet, basename='question')
router.register(r'quizzes', QuizViewSet, basename='quiz')

# NEW: Registering new viewsets for your features
router.register(r'saved-questions', SavedQuestionViewSet, basename='saved-question')
router.register(r'report-question', QuestionReportViewSet, basename='report-question')
router.register(r'exam-attempts', ExamAttemptViewSet, basename='exam-attempt')
router.register(r'quiz-categories', QuizCategoryViewSet, basename='quiz-category')
router.register(r'model-test-templates', ModelTestTemplateViewSet, basename='model-test-template')
router.register(r'daily-targets', DailyTargetViewSet, basename='daily-target')
router.register(r'daily-progress', DailyProgressViewSet, basename='daily-progress')
router.register(r'weekly-progress', WeeklyProgressViewSet, basename='weekly-progress')
router.register(r'user-activities', UserActivityViewSet, basename='user-activity')
router.register(r'streaks', StreakViewSet, basename='streak')

# ... existing urlpatterns ...
# Add these to your existing urlpatterns
urlpatterns = [
    path('', include(router.urls)),
    path('v2/exam-attempts/<int:attempt_id>/submit/', ModelTestSubmitV2View.as_view(), name='model-test-submit-v2'),
    path('v3/exam-attempts/<int:attempt_id>/submit-summary/', ModelTestSubmitSummaryV3View.as_view(), name='model-test-submit-summary-v3'),
    path('app-update/<str:platform>/', AppUpdateConfigView.as_view(), name='app-update-config'),
    
    path('import/bcstarget-topic/', BcsTargetTopicImportView.as_view(), name='bcstarget-topic-import'),
    path('import/bcstarget-answersheet/', BcsTargetAnswersheetImportView.as_view(), name='bcstarget-answersheet-import'),
    path('create-model-test/', CreateModelTestView.as_view(), name='create-model-test'),
    path('results/<int:attempt_id>/', QuizResultView.as_view(), name='quiz-result-detail'),
    path('dashboard/', ProgressDashboardView.as_view(), name='progress-dashboard'),
    path('analytics/', ProgressAnalyticsView.as_view(), name='progress-analytics'),
    
    # Authentication endpoints
    path('auth/google/', GoogleLogin.as_view(), name='google_login'),
    
    # NEW: Guest authentication endpoints
    path('auth/guest/', GuestLoginView.as_view(), name='guest_login'),
    path('auth/convert-guest/', ConvertGuestToUserView.as_view(), name='convert_guest'),
    
    # Guest stats endpoint
    path('guest/stats/', GuestUserStatsView.as_view(), name='guest_stats'),
]
