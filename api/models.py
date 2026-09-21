from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MaxValueValidator, MinValueValidator
from django.core.cache import cache
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from . import cache_utils
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal
import uuid

class Category(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    level = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        verbose_name_plural = "Categories"
        unique_together = ['name', 'parent']
        ordering = ['level', 'order', 'name']
        indexes = [
            models.Index(fields=['parent', 'level', 'order'], name='cat_parent_level_idx'),
            models.Index(fields=['level', 'order', 'name'], name='cat_level_order_idx'),
        ]

    def __str__(self):
        if self.parent:
            return f"{str(self.parent)} → {str(self.name)}"  # Explicit string conversion
        return str(self.name)  # Explicit string conversion

    def get_full_path(self):
        path = [str(self.name)]  # Explicit string conversion
        parent = self.parent
        while parent:
            path.insert(0, str(parent.name))  # Explicit string conversion
            parent = parent.parent
        return " → ".join(path)

    def get_root_category(self):
        if not self.parent:
            return self
        return self.parent.get_root_category()

    def is_leaf(self):
        return not self.children.exists()

    def get_descendants(self):
        descendants = []
        for child in self.children.all():
            descendants.append(child)
            descendants.extend(child.get_descendants())
        return descendants

    def save(self, *args, **kwargs):
        if self.parent:
            self.level = self.parent.level + 1
        else:
            self.level = 0
        super().save(*args, **kwargs)

class Label(models.Model):
    name = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return str(self.name)  # Explicit string conversion

class Question(models.Model):
    category = models.ForeignKey(Category, related_name='questions', on_delete=models.SET_NULL, null=True, blank=True)
    labels = models.ManyToManyField(Label, blank=True, help_text="Use labels for question banks, e.g., 'Previous Year 2023'")
    question_text = models.TextField(help_text="The main text of the question.")
    question_image = models.ImageField(upload_to='questions/', blank=True, null=True)
    explanation = models.TextField(blank=True, null=True)
    explanation_image = models.ImageField(upload_to='explanations/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # No `ordering` here on purpose: adding one would silently change the
        # order existing app builds receive questions in.
        indexes = [
            # Covers the category filters in QuestionViewSet, both the single
            # category and the `category_id__in=[descendants]` variant.
            models.Index(fields=['category', 'id'], name='question_cat_id_idx'),
            models.Index(fields=['created_at'], name='question_created_idx'),
            models.Index(fields=['updated_at'], name='question_updated_idx'),
        ]

    def __str__(self):
        return str(self.question_text)[:50] + "..."  # Explicit string conversion

class Answer(models.Model):
    question = models.ForeignKey(Question, related_name='answers', on_delete=models.CASCADE)
    text = models.CharField(max_length=255)
    image = models.ImageField(upload_to='answers/', blank=True, null=True)
    is_correct = models.BooleanField(default=False)

    class Meta:
        indexes = [
            # Answer prefetches always order by id within a question.
            models.Index(fields=['question', 'id'], name='answer_question_id_idx'),
            # Correct-answer lookups during grading.
            models.Index(fields=['question', 'is_correct'], name='answer_question_ok_idx'),
        ]

    def __str__(self):
        return f"Answer for Q{self.question.id}: {str(self.text)[:20]}"  # Explicit string conversion

# --- GUEST USER MODEL ---
class GuestUser(models.Model):
    """Model to track guest users"""
    guest_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    device_id = models.CharField(max_length=255, unique=True, help_text="Unique device identifier")
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)
    
    # Optional fields for guest progress (limited)
    total_questions_answered = models.PositiveIntegerField(default=0)
    total_quizzes_completed = models.PositiveIntegerField(default=0)
    
    class Meta:
        verbose_name = "Guest User"
        verbose_name_plural = "Guest Users"
        indexes = [
            # Housekeeping / analytics scans over inactive guests.
            models.Index(fields=['last_active'], name='guest_last_active_idx'),
            models.Index(fields=['created_at'], name='guest_created_idx'),
        ]
    
    def __str__(self):
        return f"Guest {str(self.guest_id)} ({str(self.device_id)[:10]}...)"  # Explicit string conversion
    
    def is_guest(self):
        return True


class AppUpdateConfig(models.Model):
    class Platform(models.TextChoices):
        ANDROID = 'android', 'Android'
        IOS = 'ios', 'iOS'

    platform = models.CharField(max_length=20, choices=Platform.choices)
    latest_version = models.CharField(max_length=32)
    latest_build_number = models.PositiveIntegerField()
    minimum_supported_build_number = models.PositiveIntegerField(default=1)
    force_update = models.BooleanField(default=False)
    title = models.CharField(max_length=120, default='New update available')
    message = models.TextField(default='Update now for the latest improvements.')
    release_notes = models.JSONField(default=list, blank=True)
    update_url = models.URLField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['platform', '-latest_build_number']
        indexes = [
            models.Index(
                fields=['platform', 'is_active', '-latest_build_number'],
                name='app_update_lookup_idx',
            ),
        ]
        verbose_name = 'App Update Config'
        verbose_name_plural = 'App Update Configs'

    def __str__(self):
        mode = 'force' if self.force_update else 'optional'
        return f'{self.get_platform_display()} {self.latest_version}+{self.latest_build_number} ({mode})'

# --- QUIZ CATEGORIZATION MODEL ---
class QuizCategory(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    level = models.PositiveIntegerField(default=0, editable=False)
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        verbose_name = "Quiz Category"
        verbose_name_plural = "Quiz Categories"
        unique_together = ['name', 'parent']
        ordering = ['level', 'order', 'name']
        indexes = [
            models.Index(fields=['parent', 'level', 'order'], name='quizcat_parent_level_idx'),
            models.Index(fields=['level', 'order', 'name'], name='quizcat_level_order_idx'),
        ]

    def __str__(self):
        if self.parent:
            return f"{str(self.parent)} → {str(self.name)}"  # Explicit string conversion
        return str(self.name)  # Explicit string conversion

    def save(self, *args, **kwargs):
        if self.parent:
            self.level = self.parent.level + 1
        else:
            self.level = 0
        super().save(*args, **kwargs)


class ModelTestTemplate(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    duration_minutes = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    correct_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    wrong_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('-0.25'),
        validators=[MaxValueValidator(Decimal('0'))],
    )
    unanswered_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MaxValueValidator(Decimal('0'))],
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'name']
        indexes = [
            # ModelTestTemplateViewSet lists active templates in this exact order.
            models.Index(
                fields=['is_active', 'display_order', 'name'],
                name='mtt_active_order_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name='mtt_duration_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(correct_mark__gte=0),
                name='mtt_correct_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(wrong_mark__lte=0),
                name='mtt_wrong_nonpositive',
            ),
            models.CheckConstraint(
                condition=models.Q(unanswered_mark__lte=0),
                name='mtt_unanswered_nonpositive',
            ),
        ]

    @property
    def total_questions(self):
        return sum(
            configuration.question_count
            for configuration in self.category_configurations.all()
        )

    def __str__(self):
        return str(self.name)


class ModelTestTemplateCategory(models.Model):
    template = models.ForeignKey(
        ModelTestTemplate,
        on_delete=models.CASCADE,
        related_name='category_configurations',
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='model_test_template_configurations',
    )
    question_count = models.PositiveIntegerField(
        validators=[MinValueValidator(1)],
    )
    include_subcategories = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'category__name']
        indexes = [
            models.Index(fields=['template', 'order'], name='mttc_template_order_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['template', 'category'],
                name='unique_template_category',
            ),
            models.CheckConstraint(
                condition=models.Q(question_count__gt=0),
                name='mttc_question_count_positive',
            ),
        ]

    @property
    def category_full_path(self):
        return self.category.get_full_path()

    def __str__(self):
        return f"{self.template}: {self.category_full_path} ({self.question_count})"

class Quiz(models.Model):
    class QuizType(models.TextChoices):
        PRACTICE = 'PRACTICE', 'Practice'
        MODEL_TEST = 'MODEL_TEST', 'Model Test'
        QUESTION_BANK = 'QUESTION_BANK', 'Question Bank'

    # --- MODIFIED: Added ForeignKey to the new QuizCategory model ---
    category = models.ForeignKey(QuizCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='quizzes')
    
    name = models.CharField(max_length=200)
    questions = models.ManyToManyField(Question, related_name='quizzes')
    quiz_type = models.CharField(max_length=20, choices=QuizType.choices, default=QuizType.PRACTICE)
    duration_minutes = models.PositiveIntegerField(default=10, help_text="Duration of the quiz in minutes.")
    source_template = models.ForeignKey(
        ModelTestTemplate,
        on_delete=models.PROTECT,
        related_name='generated_quizzes',
        null=True,
        blank=True,
    )
    correct_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('1.00'),
        validators=[MinValueValidator(Decimal('0'))],
    )
    wrong_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MaxValueValidator(Decimal('0'))],
    )
    unanswered_mark = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal('0'),
        validators=[MaxValueValidator(Decimal('0'))],
    )
    generated_for_user = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='generated_quizzes',
        null=True,
        blank=True,
    )
    generated_for_guest = models.ForeignKey(
        GuestUser,
        on_delete=models.PROTECT,
        related_name='generated_quizzes',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['quiz_type', 'category'], name='quiz_type_cat_idx'),
            models.Index(
                fields=['generated_for_user', 'generated_for_guest', 'quiz_type'],
                name='quiz_owner_type_idx',
            ),
            models.Index(fields=['created_at'], name='quiz_created_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~(
                    models.Q(generated_for_user__isnull=False)
                    & models.Q(generated_for_guest__isnull=False)
                ),
                name='quiz_generated_owner_not_both',
            ),
            models.CheckConstraint(
                condition=models.Q(correct_mark__gte=0),
                name='quiz_correct_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(wrong_mark__lte=0),
                name='quiz_wrong_nonpositive',
            ),
            models.CheckConstraint(
                condition=models.Q(unanswered_mark__lte=0),
                name='quiz_unanswered_nonpositive',
            ),
        ]

    @property
    def maximum_score(self):
        return self.questions.count() * self.correct_mark

    def __str__(self):
        return str(self.name)  # Explicit string conversion

class PreviousYearQuiz(Quiz):
    class Meta:
        proxy = True
        verbose_name = 'Previous Year Quiz'
        verbose_name_plural = 'Previous Year Quizzes'

    def save(self, *args, **kwargs):
        self.quiz_type = self.QuizType.QUESTION_BANK
        super().save(*args, **kwargs)

class QuizAttempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quiz_attempts', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='quiz_attempts', null=True, blank=True)
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    is_completed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_completed', 'start_time'], name='attempt_user_state_idx'),
            models.Index(fields=['guest_user', 'is_completed', 'start_time'], name='attempt_guest_state_idx'),
            models.Index(fields=['quiz', 'is_completed'], name='attempt_quiz_state_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='quiz_attempt_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='quiz_attempt_cannot_have_both_user_and_guest'
            )
        ]

    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        return f"{user_identifier}'s attempt on {str(self.quiz.name)}"  # Explicit string conversion

class UserSubmission(models.Model):
    attempt = models.ForeignKey(QuizAttempt, related_name='submissions', on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_answer = models.ForeignKey(Answer, on_delete=models.CASCADE, null=True)
    is_correct = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('attempt', 'question')
        indexes = [
            models.Index(fields=['attempt', 'is_correct'], name='submission_attempt_ok_idx'),
            models.Index(fields=['question'], name='submission_question_idx'),
        ]

    def __str__(self):
        return f"Submission for {str(self.attempt)} - Q{self.question.id}"  # Explicit string conversion

class SavedQuestion(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='saved_questions', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='saved_questions', null=True, blank=True)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='saved_by_users')
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'question'], name='saved_user_question_idx'),
            models.Index(fields=['guest_user', 'question'], name='saved_guest_question_idx'),
            models.Index(fields=['saved_at'], name='saved_at_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='saved_question_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='saved_question_cannot_have_both_user_and_guest'
            ),
            models.UniqueConstraint(
                fields=['user', 'question'], 
                condition=models.Q(user__isnull=False),
                name='unique_user_question'
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'question'], 
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_question'
            )
        ]

    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        return f"{user_identifier} saved '{str(self.question.question_text)[:30]}...'"  # Explicit string conversion

class QuestionReport(models.Model):
    class ReportStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        REVIEWED = 'REVIEWED', 'Reviewed'
        RESOLVED = 'RESOLVED', 'Resolved'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reported_questions')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='reports')
    reason = models.TextField(help_text="User's reason for reporting the question.")
    status = models.CharField(max_length=20, choices=ReportStatus.choices, default=ReportStatus.PENDING)
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # Admin triage queue: pending reports, newest first.
            models.Index(fields=['status', 'reported_at'], name='qreport_status_time_idx'),
            models.Index(fields=['question', 'status'], name='qreport_question_idx'),
            models.Index(fields=['user', 'reported_at'], name='qreport_user_time_idx'),
        ]

    def __str__(self):
        return f"Report by {str(self.user.username)} for Q{self.question.id} ({self.get_status_display()})"

# ===================================================================
# NEW MODELS FOR DAILY TARGETS AND PROGRESS TRACKING
# ===================================================================

class DailyTarget(models.Model):
    """Model to store user's daily targets"""
    
    class TargetType(models.TextChoices):
        QUESTIONS_SOLVED = 'QUESTIONS_SOLVED', 'Questions Solved'
        MODEL_TESTS_TAKEN = 'MODEL_TESTS_TAKEN', 'Model Tests Taken'
        PRACTICE_TIME_MINUTES = 'PRACTICE_TIME_MINUTES', 'Practice Time (Minutes)'
        QUIZ_ATTEMPTS = 'QUIZ_ATTEMPTS', 'Quiz Attempts'
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_targets', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='daily_targets', null=True, blank=True)
    target_type = models.CharField(max_length=30, choices=TargetType.choices)
    target_value = models.PositiveIntegerField(help_text="Target value (e.g., 50 questions, 2 tests, 120 minutes)")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='daily_target_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='daily_target_cannot_have_both_user_and_guest'
            ),
            models.UniqueConstraint(
                fields=['user', 'target_type'], 
                condition=models.Q(user__isnull=False),
                name='unique_user_target_type'
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'target_type'], 
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_target_type'
            )
        ]
        # The UniqueConstraints above are conditional, and MySQL cannot back
        # conditional constraints with an index, so the lookup paths get plain
        # indexes of their own.
        indexes = [
            models.Index(fields=['user', 'is_active'], name='dtarget_user_active_idx'),
            models.Index(fields=['guest_user', 'is_active'], name='dtarget_guest_active_idx'),
            models.Index(fields=['user', 'target_type'], name='dtarget_user_type_idx'),
            models.Index(fields=['guest_user', 'target_type'], name='dtarget_guest_type_idx'),
        ]
        verbose_name = "Daily Target"
        verbose_name_plural = "Daily Targets"
    
    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        target_display = self.get_target_type_display()
        return f"{user_identifier} - {target_display}: {self.target_value}"

class DailyProgress(models.Model):
    """Model to track daily progress for each target"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_progress', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='daily_progress', null=True, blank=True)
    target_type = models.CharField(max_length=30, choices=DailyTarget.TargetType.choices)
    date = models.DateField(default=date.today)
    current_value = models.PositiveIntegerField(default=0)
    target_value = models.PositiveIntegerField(help_text="Target value for this date")
    is_completed = models.BooleanField(default=False)
    completion_percentage = models.FloatField(default=0.0)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='daily_progress_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='daily_progress_cannot_have_both_user_and_guest'
            ),
            models.UniqueConstraint(
                fields=['user', 'target_type', 'date'], 
                condition=models.Q(user__isnull=False),
                name='unique_user_target_date'
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'target_type', 'date'], 
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_target_date'
            )
        ]
        indexes = [
            # today() / week() / month() all filter by owner then date range.
            models.Index(fields=['user', 'date'], name='dprogress_user_date_idx'),
            models.Index(fields=['guest_user', 'date'], name='dprogress_guest_date_idx'),
            models.Index(
                fields=['user', 'target_type', 'date'],
                name='dprogress_user_type_date_idx',
            ),
            models.Index(
                fields=['guest_user', 'target_type', 'date'],
                name='dprogress_gst_type_date_idx',
            ),
        ]
        ordering = ['-date', 'target_type']
        verbose_name = "Daily Progress"
        verbose_name_plural = "Daily Progress"
    
    def save(self, *args, **kwargs):
        # Auto-calculate completion percentage and status
        if self.target_value > 0:
            self.completion_percentage = min((self.current_value / self.target_value) * 100, 100.0)
            self.is_completed = self.current_value >= self.target_value
        super().save(*args, **kwargs)
    
    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        target_display = self.get_target_type_display()
        return f"{user_identifier} - {str(self.date)} - {target_display}: {self.current_value}/{self.target_value}"

class WeeklyProgress(models.Model):
    """Model to track weekly progress summary"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='weekly_progress', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='weekly_progress', null=True, blank=True)
    week_start_date = models.DateField()  # Monday of the week
    target_type = models.CharField(max_length=30, choices=DailyTarget.TargetType.choices)
    total_target = models.PositiveIntegerField(default=0)
    total_achieved = models.PositiveIntegerField(default=0)
    days_completed = models.PositiveIntegerField(default=0)  # Number of days target was met
    completion_percentage = models.FloatField(default=0.0)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='weekly_progress_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='weekly_progress_cannot_have_both_user_and_guest'
            ),
            models.UniqueConstraint(
                fields=['user', 'week_start_date', 'target_type'], 
                condition=models.Q(user__isnull=False),
                name='unique_user_week_target'
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'week_start_date', 'target_type'], 
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_week_target'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'week_start_date'], name='wprogress_user_week_idx'),
            models.Index(
                fields=['guest_user', 'week_start_date'],
                name='wprogress_guest_week_idx',
            ),
        ]
        ordering = ['-week_start_date', 'target_type']
        verbose_name = "Weekly Progress"
        verbose_name_plural = "Weekly Progress"
    
    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        target_display = self.get_target_type_display()
        return f"{user_identifier} - Week of {str(self.week_start_date)} - {target_display}"

class UserActivity(models.Model):
    """Model to log user activities for progress tracking"""
    
    class ActivityType(models.TextChoices):
        QUESTION_ANSWERED = 'QUESTION_ANSWERED', 'Question Answered'
        QUIZ_COMPLETED = 'QUIZ_COMPLETED', 'Quiz Completed'
        MODEL_TEST_COMPLETED = 'MODEL_TEST_COMPLETED', 'Model Test Completed'
        PRACTICE_SESSION = 'PRACTICE_SESSION', 'Practice Session'
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='activities', null=True, blank=True)
    activity_type = models.CharField(max_length=30, choices=ActivityType.choices)
    activity_date = models.DateField(auto_now_add=True)
    activity_time = models.DateTimeField(auto_now_add=True)
    
    # Optional references to related objects
    question = models.ForeignKey(Question, on_delete=models.CASCADE, null=True, blank=True)
    quiz_attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, null=True, blank=True)
    
    # Additional metadata
    duration_minutes = models.PositiveIntegerField(null=True, blank=True, help_text="Duration of activity in minutes")
    points_earned = models.PositiveIntegerField(default=0)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='user_activity_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='user_activity_cannot_have_both_user_and_guest'
            )
        ]
        indexes = [
            # UserActivity is the highest-write table here, so keep the index
            # set tight: owner+date for the daily/summary views and
            # owner+type+date for the per-target rollups.
            models.Index(fields=['user', 'activity_date'], name='activity_user_date_idx'),
            models.Index(
                fields=['guest_user', 'activity_date'],
                name='activity_guest_date_idx',
            ),
            models.Index(
                fields=['user', 'activity_type', 'activity_date'],
                name='activity_user_type_idx',
            ),
            models.Index(
                fields=['guest_user', 'activity_type', 'activity_date'],
                name='activity_guest_type_idx',
            ),
            models.Index(fields=['activity_time'], name='activity_time_idx'),
        ]
        ordering = ['-activity_time']
        verbose_name = "User Activity"
        verbose_name_plural = "User Activities"
    
    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        activity_display = self.get_activity_type_display()
        return f"{user_identifier} - {activity_display} on {str(self.activity_date)}"

class Streak(models.Model):
    """Model to track user streaks for different activities"""
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='streaks', null=True, blank=True)
    guest_user = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='streaks', null=True, blank=True)
    target_type = models.CharField(max_length=30, choices=DailyTarget.TargetType.choices)
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_activity_date = models.DateField(null=True, blank=True)
    
    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(user__isnull=False) | models.Q(guest_user__isnull=False),
                name='streak_must_have_user_or_guest'
            ),
            models.CheckConstraint(
                check=~(models.Q(user__isnull=False) & models.Q(guest_user__isnull=False)),
                name='streak_cannot_have_both_user_and_guest'
            ),
            models.UniqueConstraint(
                fields=['user', 'target_type'], 
                condition=models.Q(user__isnull=False),
                name='unique_user_streak_type'
            ),
            models.UniqueConstraint(
                fields=['guest_user', 'target_type'], 
                condition=models.Q(guest_user__isnull=False),
                name='unique_guest_streak_type'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'target_type'], name='streak_user_type_idx'),
            models.Index(fields=['guest_user', 'target_type'], name='streak_guest_type_idx'),
        ]
        verbose_name = "Streak"
        verbose_name_plural = "Streaks"
    
    def update_streak(self, activity_date=None):
        """Update streak based on activity date"""
        if activity_date is None:
            activity_date = date.today()
        
        if self.last_activity_date is None:
            # First activity
            self.current_streak = 1
            self.longest_streak = 1
            self.last_activity_date = activity_date
        elif self.last_activity_date == activity_date:
            # Same day activity, don't change streak
            return
        elif self.last_activity_date == activity_date - timedelta(days=1):
            # Consecutive day
            self.current_streak += 1
            self.longest_streak = max(self.longest_streak, self.current_streak)
            self.last_activity_date = activity_date
        elif self.last_activity_date < activity_date - timedelta(days=1):
            # Streak broken
            self.current_streak = 1
            self.last_activity_date = activity_date
        
        self.save()
    
    def __str__(self):
        user_identifier = str(self.user.username) if self.user else f"Guest {str(self.guest_user.guest_id)}"
        target_display = self.get_target_type_display()
        return f"{user_identifier} - {target_display} - Current: {self.current_streak}, Best: {self.longest_streak}"


CATEGORY_TREE_CACHE_KEYS = [
    'api:categories:tree:v3',
    'api:quiz-categories:tree:v2',
    'api:categories:serializer-context:v1',
]


def clear_category_tree_caches():
    cache.delete_many(CATEGORY_TREE_CACHE_KEYS)


@receiver([post_save, post_delete], sender=Category)
@receiver([post_save, post_delete], sender=QuizCategory)
@receiver([post_save, post_delete], sender=Question)
def invalidate_category_tree_cache(sender, **kwargs):
    clear_category_tree_caches()


# ===================================================================
# CACHED RESPONSE INVALIDATION
# ===================================================================
# Content here is written by staff and read by everyone, so cached payloads are
# invalidated on write rather than relying on TTL expiry alone.

@receiver([post_save, post_delete], sender=Category)
def invalidate_category_response_caches(sender, **kwargs):
    cache_utils.invalidate_many([
        cache_utils.NS_CATEGORY,
        cache_utils.NS_QUESTION,
        cache_utils.NS_MODEL_TEST,
    ])


@receiver([post_save, post_delete], sender=QuizCategory)
def invalidate_quiz_category_response_caches(sender, **kwargs):
    cache_utils.invalidate_many([
        cache_utils.NS_QUIZ_CATEGORY,
        cache_utils.NS_QUIZ,
    ])


@receiver([post_save, post_delete], sender=Question)
@receiver([post_save, post_delete], sender=Answer)
@receiver([post_save, post_delete], sender=Label)
def invalidate_question_response_caches(sender, **kwargs):
    cache_utils.invalidate_many([
        cache_utils.NS_QUESTION,
        cache_utils.NS_QUIZ,
    ])


@receiver([post_save, post_delete], sender=Quiz)
def invalidate_quiz_response_caches(sender, instance, **kwargs):
    # Model-test and practice runs create a Quiz row per user, but those rows
    # are excluded from the cached public listings. Skipping them keeps a busy
    # exam session from flushing the cache on every start.
    if instance.generated_for_user_id or instance.generated_for_guest_id:
        return
    cache_utils.invalidate(cache_utils.NS_QUIZ)


@receiver(m2m_changed, sender=Quiz.questions.through)
def invalidate_quiz_question_link_caches(sender, instance, action, **kwargs):
    if action not in {'post_add', 'post_remove', 'post_clear'}:
        return
    if isinstance(instance, Quiz) and (
        instance.generated_for_user_id or instance.generated_for_guest_id
    ):
        return
    cache_utils.invalidate_many([cache_utils.NS_QUIZ, cache_utils.NS_QUESTION])


@receiver([post_save, post_delete], sender=ModelTestTemplate)
@receiver([post_save, post_delete], sender=ModelTestTemplateCategory)
def invalidate_model_test_response_caches(sender, **kwargs):
    cache_utils.invalidate(cache_utils.NS_MODEL_TEST)


@receiver([post_save, post_delete], sender=AppUpdateConfig)
def invalidate_app_update_caches(sender, **kwargs):
    cache_utils.invalidate(cache_utils.NS_APP_UPDATE)
