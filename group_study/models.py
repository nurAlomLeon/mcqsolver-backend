from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from api.models import Question, Quiz


class StudyGroup(models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='groups_created',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        indexes = [
            models.Index(
                fields=['created_by', 'created_at'],
                name='group_creator_created_idx',
            ),
            models.Index(
                fields=['is_active', 'name', 'id'],
                name='group_state_name_idx',
            ),
        ]

    def __str__(self):
        return self.name


class StudyGroupMembership(models.Model):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MEMBER = 'MEMBER', 'Member'

    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='study_group_memberships',
    )
    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.MEMBER,
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Used to compute the unread chat badge. Null means "never read".
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['joined_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['group', 'user'],
                name='unique_study_group_membership',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'group'],
                name='membership_user_group_idx',
            ),
            models.Index(
                fields=['group', 'role'],
                name='membership_group_role_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} in {self.group}'


class GroupStudyQuiz(models.Model):
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='quizzes',
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='group_study_quizzes',
    )
    source_quiz = models.ForeignKey(
        Quiz,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=10)
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
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name='group_quiz_duration_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(correct_mark__gte=0),
                name='group_quiz_correct_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(wrong_mark__lte=0),
                name='group_quiz_wrong_nonpositive',
            ),
            models.CheckConstraint(
                condition=models.Q(unanswered_mark__lte=0),
                name='group_quiz_unanswered_nonpositive',
            ),
            models.CheckConstraint(
                condition=models.Q(end_at__gt=models.F('start_at')),
                name='group_quiz_window_valid',
            ),
        ]
        indexes = [
            models.Index(
                fields=['group', 'is_published', 'start_at'],
                name='group_quiz_state_start_idx',
            ),
            models.Index(
                fields=['start_at', 'end_at'],
                name='group_quiz_window_idx',
            ),
        ]

    @property
    def maximum_score(self):
        question_count = getattr(self, 'question_count', None)
        if question_count is None:
            question_count = self.questions.count()
        return question_count * self.correct_mark

    def __str__(self):
        return self.name


class GroupStudyQuestion(models.Model):
    quiz = models.ForeignKey(
        GroupStudyQuiz,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    position = models.PositiveIntegerField()
    question_text = models.TextField()
    explanation = models.TextField(blank=True)
    source_question = models.ForeignKey(
        Question,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['quiz', 'position'],
                name='unique_group_question_position',
            ),
        ]
        indexes = [
            models.Index(
                fields=['quiz', 'position'],
                name='group_question_quiz_pos_idx',
            ),
        ]

    def __str__(self):
        return self.question_text[:80]


class GroupStudyAnswer(models.Model):
    question = models.ForeignKey(
        GroupStudyQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    position = models.PositiveIntegerField()
    text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ['position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['question', 'position'],
                name='unique_group_answer_position',
            ),
        ]
        indexes = [
            models.Index(
                fields=['question', 'position'],
                name='group_answer_question_pos_idx',
            ),
        ]

    def __str__(self):
        return self.text[:80]


class GroupStudyQuizAttempt(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_study_attempts',
    )
    quiz = models.ForeignKey(
        GroupStudyQuiz,
        on_delete=models.PROTECT,
        related_name='attempts',
    )
    start_time = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    end_time = models.DateTimeField(null=True, blank=True)
    score = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'))
    is_completed = models.BooleanField(default=False)
    total_questions = models.PositiveIntegerField()
    correct_mark = models.DecimalField(max_digits=8, decimal_places=2)
    wrong_mark = models.DecimalField(max_digits=8, decimal_places=2)
    unanswered_mark = models.DecimalField(max_digits=8, decimal_places=2)

    class Meta:
        ordering = ['-start_time', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'quiz'],
                condition=models.Q(is_completed=False),
                name='unique_active_group_attempt',
            ),
            models.CheckConstraint(
                condition=models.Q(total_questions__gt=0),
                name='group_attempt_total_positive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'is_completed', 'start_time'],
                name='group_attempt_user_state_idx',
            ),
            models.Index(
                fields=['quiz', 'is_completed', 'start_time'],
                name='group_attempt_quiz_state_idx',
            ),
            models.Index(
                fields=['quiz', 'user', 'is_completed'],
                name='group_attempt_quiz_user_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} - {self.quiz}'


class GroupStudyQuizSubmission(models.Model):
    attempt = models.ForeignKey(
        GroupStudyQuizAttempt,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    question = models.ForeignKey(
        GroupStudyQuestion,
        on_delete=models.PROTECT,
        related_name='submissions',
    )
    selected_answer = models.ForeignKey(
        GroupStudyAnswer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='selected_in_submissions',
    )
    is_correct = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['question__position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['attempt', 'question'],
                name='unique_group_attempt_question_submission',
            ),
        ]
        indexes = [
            models.Index(
                fields=['attempt', 'is_correct'],
                name='group_submit_att_ok_idx',
            ),
            models.Index(
                fields=['question'],
                name='group_submit_question_idx',
            ),
        ]

    def __str__(self):
        return f'{self.attempt} - Q{self.question_id}'


class GroupMessage(models.Model):
    group = models.ForeignKey(
        StudyGroup,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='group_study_messages',
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(
                fields=['group', 'created_at'],
                name='group_message_group_time_idx',
            ),
            models.Index(
                fields=['sender', 'created_at'],
                name='group_message_sender_time_idx',
            ),
        ]

    def __str__(self):
        return f'{self.sender} in {self.group}: {self.body[:40]}'
