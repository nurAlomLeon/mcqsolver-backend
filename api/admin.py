# admin.py - Final Corrected File

import csv
import io
from django.contrib import admin, messages
from django.urls import path
from django.shortcuts import render, redirect
from django.db import transaction
from django.http import Http404
from .models import (
    Category, GuestUser, Label, Question, Answer, Quiz, SavedQuestion, 
    QuestionReport, PreviousYearQuiz, QuizAttempt, QuizCategory,
    UserSubmission, DailyTarget, DailyProgress, WeeklyProgress, 
    UserActivity, Streak, ModelTestTemplate, ModelTestTemplateCategory,
    AppUpdateConfig,
)
from .forms import (
    CsvImportForm, ModelTestTemplateCategoryForm,
    ModelTestTemplateCategoryInlineFormSet, ModelTestTemplateForm,
)

# Hierarchical Category Admin
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'get_full_path', 'level', 'parent', 'order', 'get_question_count']
    list_filter = ['level', 'parent']
    search_fields = ['name']
    ordering = ['level', 'order', 'name']
    list_editable = ['order']
    
    def get_full_path(self, obj):
        return obj.get_full_path()
    get_full_path.short_description = 'Full Path'
    
    def get_question_count(self, obj):
        if obj.is_leaf():
            return obj.questions.count()
        else:
            # Count questions in all descendant leaf categories
            descendant_ids = [desc.id for desc in obj.get_descendants() if desc.is_leaf()]
            return Question.objects.filter(category_id__in=descendant_ids).count()
    get_question_count.short_description = 'Questions'
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('parent')

    fieldsets = (
        (None, {
            'fields': ('name', 'parent', 'order')
        }),
        ('Advanced', {
            'fields': ('level',),
            'classes': ('collapse',),
            'description': 'Level is auto-calculated based on parent. 0=Subject, 1=Part, 2=Topic'
        })
    )
    
    readonly_fields = ['level']

@admin.register(Label)
class LabelAdmin(admin.ModelAdmin):
    list_display = ['name']
    search_fields = ['name']


@admin.register(AppUpdateConfig)
class AppUpdateConfigAdmin(admin.ModelAdmin):
    list_display = [
        'platform', 'latest_version', 'latest_build_number',
        'minimum_supported_build_number', 'force_update', 'is_active',
        'updated_at',
    ]
    list_filter = ['platform', 'force_update', 'is_active']
    search_fields = ['latest_version', 'title', 'message']
    ordering = ['platform', '-latest_build_number']
    list_editable = ['force_update', 'is_active']
    fieldsets = (
        ('Version', {
            'fields': (
                'platform', 'latest_version', 'latest_build_number',
                'minimum_supported_build_number', 'force_update', 'is_active',
            ),
        }),
        ('Message', {
            'fields': ('title', 'message', 'release_notes', 'update_url'),
        }),
    )

class AnswerInline(admin.TabularInline):
    model = Answer
    extra = 4
    max_num = 6

@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    inlines = [AnswerInline]
    list_display = ['question_text_short', 'get_category_path', 'created_at']
    list_filter = ['category__level', 'category', 'labels', 'created_at']
    search_fields = ['question_text', 'category__name']
    filter_horizontal = ['labels']
    change_list_template = "admin/question_changelist.html"
    
    def question_text_short(self, obj):
        return obj.question_text[:100] + "..." if len(obj.question_text) > 100 else obj.question_text
    question_text_short.short_description = 'Question'
    
    def get_category_path(self, obj):
        if obj.category:
            return obj.category.get_full_path()
        return "No Category"
    get_category_path.short_description = 'Category Path'

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('import-csv/', self.admin_site.admin_view(self.import_csv), name='api_question_import_csv'),
        ]
        return my_urls + urls

    def import_csv(self, request):
        if request.method == "POST":
            form = CsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES["csv_file"]
                selected_category = form.cleaned_data.get('category')
                
                try:
                    with transaction.atomic():
                        decoded_file = io.TextIOWrapper(csv_file.file, encoding='utf-8-sig')
                        reader = csv.DictReader(decoded_file)
                        
                        required_columns = {'Item Type', 'Question Title', 'Answer Text', 'Answer Correct/InCorrect'}
                        if not required_columns.issubset(reader.fieldnames):
                            missing = required_columns - set(reader.fieldnames)
                            self.message_user(request, f"Missing required columns from CSV: {', '.join(missing)}", messages.ERROR)
                            return redirect("..")
                        
                        questions_created = 0
                        answers_created = 0
                        current_question = None
                        
                        for row in reader:
                            item_type = row.get('Item Type', '').strip().lower()
                            
                            if item_type == 'question':
                                question_text = row.get('Question Title', '').strip()
                                if not question_text:
                                    continue
                                
                                labels_str = row.get('Hints', '').strip()
                                
                                # Use selected category from form, or fall back to CSV column
                                if selected_category:
                                    category = selected_category
                                else:
                                    category_name = row.get('Categories', '').strip()
                                    category = None
                                    if category_name:
                                        category, created = Category.objects.get_or_create(
                                            name=category_name,
                                            defaults={'level': 2}
                                        )
                                
                                current_question = Question.objects.create(
                                    question_text=question_text,
                                    explanation=row.get('Question Answer Info', '').strip(),
                                    category=category
                                )
                                questions_created += 1

                                if labels_str:
                                    label_names = [name.strip() for name in labels_str.split(',') if name.strip()]
                                    for name in label_names:
                                        label, _ = Label.objects.get_or_create(name=name)
                                        current_question.labels.add(label)
                            
                            elif item_type == 'answer' and current_question:
                                answer_text = row.get('Answer Text', '').strip()
                                if not answer_text:
                                    continue
                                
                                is_correct = str(row.get('Answer Correct/InCorrect', '')).strip() == '1'
                                Answer.objects.create(
                                    question=current_question,
                                    text=answer_text,
                                    is_correct=is_correct
                                )
                                answers_created += 1
                
                    category_msg = f" into category '{selected_category.get_full_path()}'" if selected_category else ""
                    self.message_user(request, f"Successfully imported {questions_created} questions and {answers_created} answers{category_msg}.", messages.SUCCESS)
                    return redirect("..")

                except Exception as e:
                    self.message_user(request, f"An error occurred during import: {str(e)}", messages.ERROR)
        else:
            form = CsvImportForm()

        context = self.admin_site.each_context(request)
        context['opts'] = self.model._meta
        context['form'] = form
        context['title'] = "Import Questions from CSV"
        
        return render(request, "admin/csv_form.html", context)

@admin.register(Answer)
class AnswerAdmin(admin.ModelAdmin):
    list_display = ['text', 'question', 'is_correct']
    list_filter = ['is_correct']
    search_fields = ['text', 'question__question_text']

@admin.register(QuizCategory)
class QuizCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'level', 'parent', 'order']
    list_filter = ['level', 'parent']
    search_fields = ['name']
    ordering = ['level', 'order', 'name']
    list_editable = ['order']
    
    fieldsets = (
        (None, {
            'fields': ('name', 'parent', 'order')
        }),
    )
    
    readonly_fields = ['level']


class ModelTestTemplateCategoryInline(admin.TabularInline):
    model = ModelTestTemplateCategory
    form = ModelTestTemplateCategoryForm
    formset = ModelTestTemplateCategoryInlineFormSet
    fields = ('category', 'question_count', 'include_subcategories', 'order')
    extra = 1
    ordering = ('order',)


@admin.register(ModelTestTemplate)
class ModelTestTemplateAdmin(admin.ModelAdmin):
    form = ModelTestTemplateForm
    inlines = [ModelTestTemplateCategoryInline]
    list_display = [
        'name', 'duration_minutes', 'total_question_count', 'correct_mark',
        'wrong_mark', 'is_active', 'display_order', 'updated_at',
    ]
    list_filter = ['is_active', 'created_at', 'updated_at']
    search_fields = [
        'name', 'description', 'category_configurations__category__name',
    ]
    ordering = ['display_order', 'name']
    list_editable = ['is_active', 'display_order']
    actions = ['duplicate_templates']
    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'duration_minutes'),
        }),
        ('Scoring', {
            'fields': ('correct_mark', 'wrong_mark', 'unanswered_mark'),
        }),
        ('Publishing', {
            'fields': ('is_active', 'display_order'),
        }),
    )

    @admin.display(description='Questions')
    def total_question_count(self, obj):
        return obj.total_questions

    @admin.action(description='Duplicate selected templates')
    def duplicate_templates(self, request, queryset):
        duplicated = 0
        for template in queryset.prefetch_related('category_configurations'):
            configurations = list(template.category_configurations.all())
            duplicate = ModelTestTemplate.objects.create(
                name=f'{template.name} (Copy)',
                description=template.description,
                duration_minutes=template.duration_minutes,
                correct_mark=template.correct_mark,
                wrong_mark=template.wrong_mark,
                unanswered_mark=template.unanswered_mark,
                is_active=template.is_active,
                display_order=template.display_order,
            )
            ModelTestTemplateCategory.objects.bulk_create([
                ModelTestTemplateCategory(
                    template=duplicate,
                    category=configuration.category,
                    question_count=configuration.question_count,
                    include_subcategories=configuration.include_subcategories,
                    order=configuration.order,
                )
                for configuration in configurations
            ])
            duplicated += 1
        self.message_user(request, f'Successfully duplicated {duplicated} template(s).')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related(
            'category_configurations',
        ).distinct()

@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    filter_horizontal = ('questions',)
    list_display = ['name', 'category', 'quiz_type', 'duration_minutes', 'get_question_count', 'created_at']
    list_filter = ['quiz_type', 'category', 'created_at']
    search_fields = ['name', 'category__name']
    change_form_template = "admin/quiz_change_form.html"
    actions = ['duplicate_with_questions', 'duplicate_without_questions']
    fields = (
        'name', 'category', 'quiz_type', 'duration_minutes',
        'correct_mark', 'wrong_mark', 'unanswered_mark', 'questions',
    )

    def get_question_count(self, obj):
        return obj.questions.count()
    get_question_count.short_description = 'Questions'

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(quiz_type=Quiz.QuizType.QUESTION_BANK)

    def get_urls(self):
        urls = super().get_urls()
        my_urls = [
            path('<int:object_id>/change/import-questions/', self.admin_site.admin_view(self.import_questions_from_csv), name='quiz_import_questions'),
        ]
        return my_urls + urls

    def duplicate_with_questions(self, request, queryset):
        for quiz in queryset:
            original_questions = list(quiz.questions.all())
            quiz.pk = None
            quiz.name = f"{quiz.name} (Copy)"
            quiz.save()
            quiz.questions.set(original_questions)
        self.message_user(request, f"Successfully duplicated {queryset.count()} quiz(zes) with their questions.", messages.SUCCESS)
    duplicate_with_questions.short_description = "Duplicate selected quizzes with questions"

    def duplicate_without_questions(self, request, queryset):
        for quiz in queryset:
            quiz.pk = None
            quiz.name = f"{quiz.name} (Copy)"
            quiz.save()
        self.message_user(request, f"Successfully duplicated {queryset.count()} quiz(zes) without their questions.", messages.SUCCESS)
    duplicate_without_questions.short_description = "Duplicate selected quizzes without questions"

    def import_questions_from_csv(self, request, object_id):
        try:
            quiz = self.get_object(request, object_id)
        except self.model.DoesNotExist:
            raise Http404('Quiz not found')

        if request.method == "POST":
            form = CsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                csv_file = request.FILES["csv_file"]
                selected_category = form.cleaned_data.get('category')
                try:
                    with transaction.atomic():
                        decoded_file = io.TextIOWrapper(csv_file.file, encoding='utf-8-sig')
                        reader = csv.DictReader(decoded_file)
                        
                        required_columns = {'Item Type', 'Question Title', 'Answer Text', 'Answer Correct/InCorrect'}
                        if not required_columns.issubset(reader.fieldnames):
                            missing = required_columns - set(reader.fieldnames)
                            self.message_user(request, f"Missing columns: {', '.join(missing)}", messages.ERROR)
                            return redirect(".")

                        new_questions = []
                        current_question = None
                        for row in reader:
                            item_type = row.get('Item Type', '').strip().lower()
                            if item_type == 'question':
                                question_text = row.get('Question Title', '').strip()
                                if not question_text: 
                                    continue
                                
                                # Use selected category from form, or fall back to CSV column
                                if selected_category:
                                    category = selected_category
                                else:
                                    category_name = row.get('Categories', '').strip()
                                    category = None
                                    if category_name:
                                        category, _ = Category.objects.get_or_create(
                                            name=category_name,
                                            defaults={'level': 2}
                                        )
                                
                                current_question = Question.objects.create(
                                    question_text=question_text,
                                    explanation=row.get('Question Answer Info', '').strip(),
                                    category=category
                                )
                                new_questions.append(current_question)

                            elif item_type == 'answer' and current_question:
                                answer_text = row.get('Answer Text', '').strip()
                                if not answer_text: 
                                    continue
                                
                                is_correct = str(row.get('Answer Correct/InCorrect', '')).strip() == '1'
                                Answer.objects.create(
                                    question=current_question, 
                                    text=answer_text, 
                                    is_correct=is_correct
                                )
                        
                        quiz.questions.add(*new_questions)
                        self.message_user(request, f"Successfully imported and added {len(new_questions)} questions to the quiz.", messages.SUCCESS)
                        return redirect('..')
                except Exception as e:
                    self.message_user(request, f"An error occurred: {e}", messages.ERROR)
        else:
            form = CsvImportForm()

        context = self.admin_site.each_context(request)
        context['opts'] = self.model._meta
        context['form'] = form
        context['title'] = f"Import Questions for: {quiz.name}"
        context['quiz'] = quiz
        return render(request, "admin/quiz_import_form.html", context)

@admin.register(PreviousYearQuiz)
class PreviousYearQuizAdmin(QuizAdmin):
    def get_queryset(self, request):
        return super(QuizAdmin, self).get_queryset(request).filter(quiz_type=Quiz.QuizType.QUESTION_BANK)

    def save_model(self, request, obj, form, change):
        obj.quiz_type = Quiz.QuizType.QUESTION_BANK
        super().save_model(request, obj, form, change)

@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'quiz', 'score', 'is_completed', 'start_time']
    list_filter = ['is_completed', 'quiz__quiz_type', 'start_time']
    search_fields = ['user__username', 'guest_user__guest_id', 'quiz__name']
    readonly_fields = ['start_time', 'end_time']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(SavedQuestion)
class SavedQuestionAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'question_short', 'saved_at']
    list_filter = ['saved_at']
    search_fields = ['user__username', 'guest_user__guest_id', 'question__question_text']
    
    def question_short(self, obj):
        return obj.question.question_text[:50] + "..."
    question_short.short_description = 'Question'
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(QuestionReport)
class QuestionReportAdmin(admin.ModelAdmin):
    list_display = ['user', 'question_short', 'status', 'reported_at']
    list_filter = ['status', 'reported_at']
    search_fields = ['user__username', 'question__question_text', 'reason']
    list_editable = ['status']
    
    def question_short(self, obj):
        return obj.question.question_text[:50] + "..."
    question_short.short_description = 'Question'

@admin.register(GuestUser)
class GuestUserAdmin(admin.ModelAdmin):
    list_display = ['guest_id', 'device_id_short', 'total_questions_answered', 'total_quizzes_completed', 'created_at', 'last_active']
    list_filter = ['created_at', 'last_active']
    search_fields = ['guest_id', 'device_id']
    readonly_fields = ['guest_id', 'created_at', 'last_active']
    
    def device_id_short(self, obj):
        return f"{obj.device_id[:20]}..." if len(obj.device_id) > 20 else obj.device_id
    device_id_short.short_description = 'Device ID'

@admin.register(UserSubmission)
class UserSubmissionAdmin(admin.ModelAdmin):
    list_display = ['attempt', 'question', 'selected_answer', 'is_correct', 'submitted_at']
    list_filter = ['is_correct', 'submitted_at']
    search_fields = ['attempt__user__username', 'attempt__guest_user__guest_id', 'question__question_text']
    readonly_fields = ['submitted_at']

@admin.register(DailyTarget)
class DailyTargetAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'target_type', 'target_value', 'is_active', 'created_at']
    list_filter = ['target_type', 'is_active', 'created_at']
    search_fields = ['user__username', 'guest_user__guest_id']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(DailyProgress)
class DailyProgressAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'target_type', 'date', 'current_value', 'target_value', 'is_completed', 'completion_percentage']
    list_filter = ['target_type', 'date', 'is_completed']
    search_fields = ['user__username', 'guest_user__guest_id']
    readonly_fields = ['completion_percentage']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(WeeklyProgress)
class WeeklyProgressAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'target_type', 'week_start_date', 'total_achieved', 'total_target', 'days_completed', 'completion_percentage']
    list_filter = ['target_type', 'week_start_date']
    search_fields = ['user__username', 'guest_user__guest_id']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(UserActivity)
class UserActivityAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'activity_type', 'activity_date', 'activity_time', 'duration_minutes', 'points_earned']
    list_filter = ['activity_type', 'activity_date', 'activity_time']
    search_fields = ['user__username', 'guest_user__guest_id']
    readonly_fields = ['activity_date', 'activity_time']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'

@admin.register(Streak)
class StreakAdmin(admin.ModelAdmin):
    list_display = ['get_user_identifier', 'target_type', 'current_streak', 'longest_streak', 'last_activity_date']
    list_filter = ['target_type', 'last_activity_date']
    search_fields = ['user__username', 'guest_user__guest_id']
    readonly_fields = ['last_activity_date']
    
    def get_user_identifier(self, obj):
        if obj.user:
            return obj.user.username
        return f"Guest {obj.guest_user.guest_id}"
    get_user_identifier.short_description = 'User'
