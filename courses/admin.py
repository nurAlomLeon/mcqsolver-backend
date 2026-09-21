from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import path

from .models import (
    Course,
    CourseAnswer,
    CourseEnrollment,
    CourseQuestion,
    CourseQuiz,
    CourseQuizAttempt,
    CourseQuizSubmission,
)
from .services import auto_reorganize_course_quizzes, reorder_course_quizzes


class CourseAnswerInline(admin.TabularInline):
    model = CourseAnswer
    extra = 1


class CourseQuestionInline(admin.StackedInline):
    model = CourseQuestion
    extra = 0
    show_change_link = True


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('name', 'course_type', 'is_published', 'allow_self_enrollment', 'created_by', 'created_at')
    list_filter = ('course_type', 'is_published', 'allow_self_enrollment')
    search_fields = ('name', 'description')
    change_form_template = 'admin/courses/course/change_form.html'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                '<path:object_id>/reorganize-exams/',
                self.admin_site.admin_view(self.reorganize_exams_view),
                name='courses_course_reorganize_exams',
            ),
        ]
        return custom_urls + urls

    def reorganize_exams_view(self, request, object_id):
        course = get_object_or_404(Course, pk=object_id)
        if not self.has_change_permission(request, course):
            raise PermissionDenied

        change_url = redirect('admin:courses_course_change', object_id=course.pk)
        if request.method != 'POST':
            return change_url

        quizzes = list(course.quizzes.all())

        if 'auto' in request.POST:
            try:
                auto_reorganize_course_quizzes(course)
            except ValidationError as exc:
                for message in exc.messages:
                    messages.error(request, message)
            else:
                messages.success(request, 'Exams were auto-reorganized.')
            return change_url

        position_by_quiz_id = {}
        for quiz in quizzes:
            raw_position = request.POST.get(f'position_{quiz.id}')
            try:
                position = int(raw_position)
            except (TypeError, ValueError):
                messages.error(request, f'Position for "{quiz.name}" must be a number.')
                return change_url
            position_by_quiz_id[quiz.id] = position

        if len(set(position_by_quiz_id.values())) != len(quizzes):
            messages.error(request, 'Exam positions must be unique.')
            return change_url

        ordered_ids = [
            quiz_id for quiz_id, _ in sorted(
                position_by_quiz_id.items(),
                key=lambda item: item[1],
            )
        ]

        try:
            reorder_course_quizzes(course, ordered_ids)
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        else:
            messages.success(request, 'Exam order saved.')

        return change_url


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('course', 'user', 'is_active', 'source', 'enrolled_by', 'enrolled_at')
    list_filter = ('is_active', 'source')
    search_fields = ('course__name', 'user__username')
    autocomplete_fields = ('course', 'user', 'enrolled_by')


@admin.register(CourseQuiz)
class CourseQuizAdmin(admin.ModelAdmin):
    list_display = ('name', 'course', 'quiz_type', 'position', 'duration_minutes', 'unlock_at', 'is_active')
    list_filter = ('quiz_type', 'is_active', 'course__course_type')
    search_fields = ('name', 'course__name')
    autocomplete_fields = ('course',)
    inlines = [CourseQuestionInline]


@admin.register(CourseQuestion)
class CourseQuestionAdmin(admin.ModelAdmin):
    list_display = ('question_text', 'quiz', 'position')
    list_filter = ('quiz__course',)
    search_fields = ('question_text', 'quiz__name')
    autocomplete_fields = ('quiz',)
    inlines = [CourseAnswerInline]


@admin.register(CourseAnswer)
class CourseAnswerAdmin(admin.ModelAdmin):
    list_display = ('text', 'question', 'position', 'is_correct')
    list_filter = ('is_correct', 'question__quiz__course')
    search_fields = ('text', 'question__question_text', 'question__quiz__name')
    autocomplete_fields = ('question',)


@admin.register(CourseQuizAttempt)
class CourseQuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'quiz', 'score', 'is_completed', 'start_time', 'end_time')
    list_filter = ('is_completed', 'quiz__course')
    search_fields = ('user__username', 'quiz__name', 'quiz__course__name')
    autocomplete_fields = ('user', 'quiz')
    readonly_fields = (
        'user',
        'quiz',
        'start_time',
        'expires_at',
        'end_time',
        'score',
        'total_questions',
        'correct_mark',
        'wrong_mark',
        'unanswered_mark',
    )


@admin.register(CourseQuizSubmission)
class CourseQuizSubmissionAdmin(admin.ModelAdmin):
    list_display = ('attempt', 'question', 'selected_answer', 'is_correct', 'submitted_at')
    list_filter = ('is_correct', 'attempt__quiz__course')
    search_fields = ('attempt__user__username', 'question__question_text')
    autocomplete_fields = ('attempt', 'question', 'selected_answer')
