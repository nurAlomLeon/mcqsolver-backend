from django.contrib import admin

from .models import (
    GroupMessage,
    GroupStudyAnswer,
    GroupStudyQuestion,
    GroupStudyQuiz,
    GroupStudyQuizAttempt,
    GroupStudyQuizSubmission,
    StudyGroup,
    StudyGroupMembership,
)


class StudyGroupMembershipInline(admin.TabularInline):
    model = StudyGroupMembership
    extra = 0
    autocomplete_fields = ('user',)


class GroupStudyAnswerInline(admin.TabularInline):
    model = GroupStudyAnswer
    extra = 1


class GroupStudyQuestionInline(admin.StackedInline):
    model = GroupStudyQuestion
    extra = 0
    show_change_link = True


@admin.register(StudyGroup)
class StudyGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'is_active', 'created_by', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    autocomplete_fields = ('created_by',)
    inlines = [StudyGroupMembershipInline]


@admin.register(StudyGroupMembership)
class StudyGroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('group', 'user', 'role', 'joined_at')
    list_filter = ('role',)
    search_fields = ('group__name', 'user__username', 'user__email')
    autocomplete_fields = ('group', 'user')


@admin.register(GroupStudyQuiz)
class GroupStudyQuizAdmin(admin.ModelAdmin):
    list_display = ('name', 'group', 'start_at', 'end_at', 'duration_minutes', 'is_published')
    list_filter = ('is_published', 'group')
    search_fields = ('name', 'group__name')
    autocomplete_fields = ('group', 'created_by', 'source_quiz')
    inlines = [GroupStudyQuestionInline]


@admin.register(GroupStudyQuestion)
class GroupStudyQuestionAdmin(admin.ModelAdmin):
    list_display = ('question_text', 'quiz', 'position')
    list_filter = ('quiz__group',)
    search_fields = ('question_text', 'quiz__name')
    autocomplete_fields = ('quiz', 'source_question')
    inlines = [GroupStudyAnswerInline]


@admin.register(GroupStudyAnswer)
class GroupStudyAnswerAdmin(admin.ModelAdmin):
    list_display = ('text', 'question', 'position', 'is_correct')
    list_filter = ('is_correct', 'question__quiz__group')
    search_fields = ('text', 'question__question_text', 'question__quiz__name')
    autocomplete_fields = ('question',)


@admin.register(GroupStudyQuizAttempt)
class GroupStudyQuizAttemptAdmin(admin.ModelAdmin):
    list_display = ('user', 'quiz', 'score', 'is_completed', 'start_time', 'end_time')
    list_filter = ('is_completed', 'quiz__group')
    search_fields = ('user__username', 'quiz__name', 'quiz__group__name')
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


@admin.register(GroupStudyQuizSubmission)
class GroupStudyQuizSubmissionAdmin(admin.ModelAdmin):
    list_display = ('attempt', 'question', 'selected_answer', 'is_correct', 'submitted_at')
    list_filter = ('is_correct', 'attempt__quiz__group')
    search_fields = ('attempt__user__username', 'question__question_text')
    autocomplete_fields = ('attempt', 'question', 'selected_answer')


@admin.register(GroupMessage)
class GroupMessageAdmin(admin.ModelAdmin):
    list_display = ('group', 'sender', 'body', 'created_at')
    list_filter = ('group',)
    search_fields = ('group__name', 'sender__username', 'body')
    autocomplete_fields = ('group', 'sender')
