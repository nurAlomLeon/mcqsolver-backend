from django.urls import path

from .views import (
    CourseDetailView,
    CourseEnrollView,
    CourseEnrollmentListCreateView,
    CourseListCreateView,
    CoursePerformanceView,
    CourseQuizAttemptResultView,
    CourseQuizAttemptStartView,
    CourseQuizAttemptSubmitView,
    CourseQuizDetailView,
    CourseQuizImportView,
    CourseQuizListCreateView,
    CourseQuizQuestionImportView,
)
from .bulk_explanation_update import bulk_update_course_question_explanations


urlpatterns = [
    path('', CourseListCreateView.as_view(), name='course-list-create'),
    path('<int:course_id>/', CourseDetailView.as_view(), name='course-detail'),
    path('<int:course_id>/enroll/', CourseEnrollView.as_view(), name='course-enroll'),
    path('<int:course_id>/enrollments/', CourseEnrollmentListCreateView.as_view(), name='course-enrollments'),
    path('<int:course_id>/performance/', CoursePerformanceView.as_view(), name='course-performance'),
    path('<int:course_id>/quizzes/', CourseQuizListCreateView.as_view(), name='course-quiz-list-create'),
    path('<int:course_id>/quizzes/import/', CourseQuizImportView.as_view(), name='course-quiz-import'),
    path('<int:course_id>/quizzes/<int:quiz_id>/', CourseQuizDetailView.as_view(), name='course-quiz-detail'),
    path('<int:course_id>/quizzes/<int:quiz_id>/questions/import/', CourseQuizQuestionImportView.as_view(), name='course-quiz-question-import'),
    path('<int:course_id>/quizzes/<int:quiz_id>/attempts/start/', CourseQuizAttemptStartView.as_view(), name='course-quiz-attempt-start'),
    path('attempts/<int:attempt_id>/submit/', CourseQuizAttemptSubmitView.as_view(), name='course-quiz-attempt-submit'),
    path('attempts/<int:attempt_id>/result/', CourseQuizAttemptResultView.as_view(), name='course-quiz-attempt-result'),
    
    # Bulk update explanations
    path('questions/bulk-update-explanations/', bulk_update_course_question_explanations, name='bulk-update-course-question-explanations'),
]
