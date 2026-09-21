from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from api import cache_utils


class Course(models.Model):
    class CourseType(models.TextChoices):
        ARCHIVE = 'ARCHIVE', 'Archive'
        LIVE = 'LIVE', 'Live'

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    course_type = models.CharField(
        max_length=20,
        choices=CourseType.choices,
        default=CourseType.ARCHIVE,
    )
    is_published = models.BooleanField(default=False)
    allow_self_enrollment = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='courses_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name', 'id']
        indexes = [
            models.Index(
                fields=['is_published', 'course_type', 'created_at'],
                name='course_state_type_created_idx',
            ),
            models.Index(
                fields=['created_by', 'created_at'],
                name='course_creator_created_idx',
            ),
            # Learner course list: published filter plus the default ordering.
            models.Index(
                fields=['is_published', 'name', 'id'],
                name='course_pub_name_idx',
            ),
        ]

    def __str__(self):
        return self.name


class CourseEnrollment(models.Model):
    class EnrollmentSource(models.TextChoices):
        STAFF = 'STAFF', 'Staff'
        SELF = 'SELF', 'Self'

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='enrollments',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_enrollments',
    )
    is_active = models.BooleanField(default=True)
    source = models.CharField(
        max_length=10,
        choices=EnrollmentSource.choices,
        default=EnrollmentSource.STAFF,
    )
    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='course_enrollments_created',
    )
    enrolled_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-enrolled_at', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'user'],
                name='unique_course_enrollment',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'is_active'],
                name='course_enroll_user_state_idx',
            ),
            models.Index(
                fields=['course', 'is_active'],
                name='course_enroll_course_state_idx',
            ),
            models.Index(fields=['enrolled_at'], name='course_enroll_at_idx'),
            # Home screen: the user's active enrollments, newest first.
            models.Index(
                fields=['user', 'is_active', 'enrolled_at'],
                name='crs_enr_user_state_at_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} enrolled in {self.course}'


class CourseQuiz(models.Model):
    class QuizType(models.TextChoices):
        PRACTICE = 'PRACTICE', 'Practice'
        MODEL_TEST = 'MODEL_TEST', 'Model Test'
        QUESTION_BANK = 'QUESTION_BANK', 'Question Bank'

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name='quizzes',
    )
    name = models.CharField(max_length=200)
    quiz_type = models.CharField(
        max_length=20,
        choices=QuizType.choices,
        default=QuizType.PRACTICE,
    )
    position = models.PositiveIntegerField()
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
    unlock_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    topic = models.TextField(blank=True, default='')
    source_name = models.CharField(max_length=50, blank=True, default='')
    external_quiz_id = models.CharField(max_length=100, blank=True, default='')
    source_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['course', 'position'],
                name='unique_course_quiz_position',
            ),
            models.UniqueConstraint(
                fields=['course', 'source_name', 'external_quiz_id'],
                condition=(
                    ~models.Q(source_name='') & ~models.Q(external_quiz_id='')
                ),
                name='unique_course_quiz_external_source',
            ),
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name='course_quiz_duration_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(correct_mark__gte=0),
                name='course_quiz_correct_nonnegative',
            ),
            models.CheckConstraint(
                condition=models.Q(wrong_mark__lte=0),
                name='course_quiz_wrong_nonpositive',
            ),
            models.CheckConstraint(
                condition=models.Q(unanswered_mark__lte=0),
                name='course_quiz_unanswered_nonpositive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['course', 'is_active', 'position'],
                name='crs_quiz_state_pos_idx',
            ),
            models.Index(
                fields=['course', 'is_active', 'unlock_at'],
                name='crs_quiz_unlock_idx',
            ),
            models.Index(
                fields=['course', 'source_name', 'external_quiz_id'],
                name='crs_quiz_source_idx',
            ),
            # Home screen exams span every enrolled course, so the leading
            # column has to be is_active rather than course.
            models.Index(
                fields=['is_active', 'position', 'id'],
                name='crs_quiz_active_pos_idx',
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


class CourseQuestion(models.Model):
    quiz = models.ForeignKey(
        CourseQuiz,
        on_delete=models.CASCADE,
        related_name='questions',
    )
    position = models.PositiveIntegerField()
    question_text = models.TextField()
    explanation = models.TextField(blank=True)
    source_question_id = models.CharField(max_length=100, blank=True, default='')
    source_question_url = models.URLField(blank=True)
    fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        constraints = [
            models.UniqueConstraint(
                fields=['quiz', 'position'],
                name='unique_course_question_position',
            ),
            models.UniqueConstraint(
                fields=['quiz', 'fingerprint'],
                name='unique_course_question_fingerprint',
            ),
            models.UniqueConstraint(
                fields=['quiz', 'source_question_id'],
                condition=~models.Q(source_question_id=''),
                name='unique_course_question_external_id',
            ),
        ]
        indexes = [
            models.Index(
                fields=['quiz', 'position'],
                name='course_question_quiz_pos_idx',
            ),
            models.Index(
                fields=['quiz', 'fingerprint'],
                name='course_question_quiz_fp_idx',
            ),
        ]

    def __str__(self):
        return self.question_text[:80]


class CourseAnswer(models.Model):
    question = models.ForeignKey(
        CourseQuestion,
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
                name='unique_course_answer_position',
            ),
        ]
        indexes = [
            models.Index(
                fields=['question', 'position'],
                name='course_answer_question_pos_idx',
            ),
        ]

    def __str__(self):
        return self.text[:80]


class CourseQuizAttempt(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='course_quiz_attempts',
    )
    quiz = models.ForeignKey(
        CourseQuiz,
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
                name='unique_active_course_attempt',
            ),
            models.CheckConstraint(
                condition=models.Q(total_questions__gt=0),
                name='course_attempt_total_positive',
            ),
        ]
        indexes = [
            models.Index(
                fields=['user', 'is_completed', 'start_time'],
                name='course_attempt_user_state_idx',
            ),
            models.Index(
                fields=['quiz', 'is_completed', 'start_time'],
                name='course_attempt_quiz_state_idx',
            ),
            # Backs the Exists()/Count() subqueries in annotate_quiz_attempts,
            # which run once per quiz row on the course and home screens.
            models.Index(
                fields=['quiz', 'user', 'is_completed'],
                name='crs_att_quiz_user_state_idx',
            ),
        ]

    def __str__(self):
        return f'{self.user} - {self.quiz}'


class CourseQuizSubmission(models.Model):
    attempt = models.ForeignKey(
        CourseQuizAttempt,
        on_delete=models.CASCADE,
        related_name='submissions',
    )
    question = models.ForeignKey(
        CourseQuestion,
        on_delete=models.PROTECT,
        related_name='submissions',
    )
    selected_answer = models.ForeignKey(
        CourseAnswer,
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
                name='unique_course_attempt_question_submission',
            ),
        ]
        indexes = [
            models.Index(
                fields=['attempt', 'is_correct'],
                name='crs_submit_att_ok_idx',
            ),
            models.Index(
                fields=['question'],
                name='crs_submit_question_idx',
            ),
        ]

    def __str__(self):
        return f'{self.attempt} - Q{self.question_id}'


# ===================================================================
# CACHED RESPONSE INVALIDATION
# ===================================================================

@receiver([post_save, post_delete], sender=Course)
@receiver([post_save, post_delete], sender=CourseQuiz)
def invalidate_course_home_caches(sender, **kwargs):
    """Staff edited the catalogue, so every user's home payload is stale."""
    cache_utils.invalidate(cache_utils.NS_COURSE_HOME)


@receiver([post_save, post_delete], sender=CourseEnrollment)
@receiver([post_save, post_delete], sender=CourseQuizAttempt)
def invalidate_user_course_home_cache(sender, instance, **kwargs):
    """Only this user's home payload changed, so only flush their entry."""
    if instance.user_id:
        cache_utils.invalidate(cache_utils.NS_COURSE_HOME, scope=instance.user_id)
