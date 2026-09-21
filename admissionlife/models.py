from django.contrib.auth.models import User
from django.db import models


# =============================================================================
# Question Bank Models (separate from api app — admissionlife data only)
# =============================================================================

class Category(models.Model):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey(
        'self', on_delete=models.CASCADE, null=True, blank=True, related_name='children'
    )
    level = models.PositiveIntegerField(default=0)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = 'admissionlife'
        verbose_name_plural = 'Categories'
        unique_together = ['name', 'parent']
        ordering = ['level', 'order', 'name']

    def __str__(self):
        if self.parent:
            return f"{str(self.parent)} → {str(self.name)}"
        return str(self.name)

    def get_full_path(self):
        path = [str(self.name)]
        parent = self.parent
        while parent:
            path.insert(0, str(parent.name))
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

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        return str(self.name)


class Question(models.Model):
    category = models.ForeignKey(
        Category, related_name='questions', on_delete=models.SET_NULL, null=True, blank=True
    )
    labels = models.ManyToManyField(
        Label, blank=True, help_text="Labels for question banks, e.g., 'Previous Year 2023'"
    )
    question_text = models.TextField(help_text="The main text of the question.")
    question_image = models.ImageField(upload_to='admissionlife/questions/', blank=True, null=True)
    explanation = models.TextField(blank=True, null=True)
    explanation_image = models.ImageField(upload_to='admissionlife/explanations/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        return str(self.question_text)[:50] + "..."


class Answer(models.Model):
    question = models.ForeignKey(Question, related_name='answers', on_delete=models.CASCADE)
    text = models.CharField(max_length=255)
    image = models.ImageField(upload_to='admissionlife/answers/', blank=True, null=True)
    is_correct = models.BooleanField(default=False)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        return f"Answer for Q{self.question_id}: {str(self.text)[:20]}"


class Quiz(models.Model):
    class QuizType(models.TextChoices):
        PRACTICE = 'PRACTICE', 'Practice'

    name = models.CharField(max_length=200)
    questions = models.ManyToManyField(Question, related_name='quizzes')
    quiz_type = models.CharField(
        max_length=20, choices=QuizType.choices, default=QuizType.PRACTICE
    )
    duration_minutes = models.PositiveIntegerField(
        default=10, help_text="Duration of the quiz in minutes."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        return str(self.name)


class QuizAttempt(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='admissionlife_quiz_attempts',
        null=True, blank=True
    )
    guest_user = models.ForeignKey(
        'api.GuestUser', on_delete=models.CASCADE,
        related_name='admissionlife_quiz_attempts',
        null=True, blank=True
    )
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='attempts')
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    score = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        if self.user:
            return f"{self.user.username}'s attempt on {self.quiz.name}"
        return f"Guest attempt on {self.quiz.name}"


class UserSubmission(models.Model):
    attempt = models.ForeignKey(QuizAttempt, related_name='submissions', on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_answer = models.ForeignKey(Answer, on_delete=models.CASCADE, null=True)
    is_correct = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'admissionlife'
        unique_together = ('attempt', 'question')

    def __str__(self):
        return f"Submission for attempt {self.attempt_id} - Q{self.question_id}"


class SavedQuestion(models.Model):
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='admissionlife_saved_questions',
        null=True, blank=True
    )
    guest_user = models.ForeignKey(
        'api.GuestUser', on_delete=models.CASCADE,
        related_name='admissionlife_saved_questions',
        null=True, blank=True
    )
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name='saved_by_users'
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        if self.user:
            return f"{self.user.username} saved '{str(self.question.question_text)[:30]}...'"
        return f"Guest saved '{str(self.question.question_text)[:30]}...'"


class QuestionReport(models.Model):
    class ReportStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        REVIEWED = 'REVIEWED', 'Reviewed'
        RESOLVED = 'RESOLVED', 'Resolved'

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='admissionlife_reported_questions',
        null=True, blank=True
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='reports')
    reason = models.TextField(help_text="User's reason for reporting the question.")
    status = models.CharField(
        max_length=20, choices=ReportStatus.choices, default=ReportStatus.PENDING
    )
    reported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'admissionlife'

    def __str__(self):
        return f"Report by {self.user.username} for Q{self.question_id} ({self.get_status_display()})"


# =============================================================================
# Batch / Enrollment / Exam Models
# =============================================================================

class Batch(models.Model):
    class BatchType(models.TextChoices):
        PRE_RECORDED = 'PRE_RECORDED', 'Pre-Recorded'
        LIVE = 'LIVE', 'Live'

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(max_length=2000)
    batch_type = models.CharField(max_length=15, choices=BatchType.choices)
    price = models.DecimalField(max_digits=8, decimal_places=2)  # 0.00 to 999999.99
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_active']),
            models.Index(fields=['batch_type']),
        ]

    def __str__(self):
        return self.name


class Payment(models.Model):
    class PaymentMethod(models.TextChoices):
        BKASH = 'bKash', 'bKash'
        NAGAD = 'Nagad', 'Nagad'
        ROCKET = 'Rocket', 'Rocket'
        UPAY = 'Upay', 'Upay'

    class PaymentStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admissionlife_payments')
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='payments')
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    transaction_id = models.CharField(max_length=30)
    sender_number = models.CharField(max_length=11)  # Bangladeshi mobile: 11 digits starting with "01"
    amount = models.DecimalField(max_digits=7, decimal_places=2)  # 1 to 99999
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    admin_notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('payment_method', 'transaction_id')
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['user', 'batch']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.batch.name} - {self.status}"


class Enrollment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admissionlife_enrollments')
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='enrollments')
    payment = models.OneToOneField('Payment', on_delete=models.SET_NULL, null=True, related_name='enrollment')
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'batch')

    def __str__(self):
        return f"{self.user.username} enrolled in {self.batch.name}"


class Exam(models.Model):
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='exams')
    title = models.CharField(max_length=200)
    duration_minutes = models.PositiveIntegerField()  # 1 to 300
    order = models.PositiveIntegerField()
    passing_score = models.PositiveIntegerField(default=0)  # percentage 0-100
    is_active = models.BooleanField(default=True)
    unlock_datetime = models.DateTimeField(null=True, blank=True)  # For live batches
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('batch', 'order')
        indexes = [
            models.Index(fields=['batch', 'order']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.batch.name} - {self.title}"


class ExamQuestion(models.Model):
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='questions')
    question_text = models.TextField()
    answer_1 = models.CharField(max_length=255)
    answer_2 = models.CharField(max_length=255)
    answer_3 = models.CharField(max_length=255)
    answer_4 = models.CharField(max_length=255)
    correct_answer = models.PositiveIntegerField()  # 1-4
    explanation = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Q: {self.question_text[:50]}"


class ExamAttempt(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='admissionlife_attempts')
    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name='attempts')
    score = models.DecimalField(max_digits=7, decimal_places=2, default=0)  # Can be negative
    total_questions = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    incorrect_count = models.PositiveIntegerField(default=0)
    unanswered_count = models.PositiveIntegerField(default=0)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'exam']),
            models.Index(fields=['is_completed']),
            models.Index(fields=['exam', 'is_completed', 'score']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.exam.title} - Score: {self.score}"


class ExamSubmission(models.Model):
    attempt = models.ForeignKey(ExamAttempt, on_delete=models.CASCADE, related_name='submissions')
    question = models.ForeignKey(ExamQuestion, on_delete=models.CASCADE, related_name='submissions')
    selected_answer = models.PositiveIntegerField(null=True, blank=True)  # 1-4 or null for unanswered
    is_correct = models.BooleanField(default=False)

    class Meta:
        unique_together = ('attempt', 'question')

    def __str__(self):
        return f"Attempt {self.attempt_id} - Question {self.question_id}"
