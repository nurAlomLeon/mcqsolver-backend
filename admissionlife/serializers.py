from decimal import Decimal

from rest_framework import serializers

from .models import (
    Answer, Category, Label, Question, SavedQuestion, QuestionReport,
    Quiz, QuizAttempt, UserSubmission,
    Batch, Enrollment, Exam, ExamAttempt, ExamQuestion, ExamSubmission, Payment,
)
from .validators import validate_amount, validate_bangladeshi_phone, validate_transaction_id


# =============================================================================
# Batch Serializers
# =============================================================================


class BatchListSerializer(serializers.ModelSerializer):
    """Serializer for public batch listing."""

    class Meta:
        model = Batch
        fields = ['id', 'name', 'description', 'batch_type', 'price', 'is_active']


class BatchDetailSerializer(serializers.ModelSerializer):
    """Serializer for batch detail view with exam and enrollment counts."""

    exam_count = serializers.SerializerMethodField()
    enrollment_count = serializers.SerializerMethodField()

    class Meta:
        model = Batch
        fields = [
            'id', 'name', 'description', 'batch_type', 'price',
            'is_active', 'created_at', 'updated_at',
            'exam_count', 'enrollment_count',
        ]

    def get_exam_count(self, obj):
        return obj.exams.count()

    def get_enrollment_count(self, obj):
        return obj.enrollments.count()


class BatchCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating batches with full validation."""

    class Meta:
        model = Batch
        fields = ['name', 'description', 'batch_type', 'price', 'is_active']

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field is required.")
        if len(value) > 200:
            raise serializers.ValidationError(
                "Ensure this field has no more than 200 characters."
            )
        # Check uniqueness with a friendly error message
        qs = Batch.objects.filter(name=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "A batch with this name already exists."
            )
        return value

    def validate_description(self, value):
        if value and len(value) > 2000:
            raise serializers.ValidationError(
                "Ensure this field has no more than 2000 characters."
            )
        return value

    def validate_batch_type(self, value):
        valid_choices = [choice[0] for choice in Batch.BatchType.choices]
        if value not in valid_choices:
            raise serializers.ValidationError(
                f"Invalid batch type. Must be one of: {', '.join(valid_choices)}."
            )
        return value

    def validate_price(self, value):
        if value is None:
            raise serializers.ValidationError("This field is required.")
        if value < Decimal('0.00'):
            raise serializers.ValidationError(
                "Price must be at least 0.00."
            )
        if value > Decimal('999999.99'):
            raise serializers.ValidationError(
                "Price must be no more than 999999.99."
            )
        return value


# =============================================================================
# Payment Serializers
# =============================================================================


class PaymentSubmitSerializer(serializers.Serializer):
    """Serializer for submitting a new payment."""

    batch = serializers.PrimaryKeyRelatedField(queryset=Batch.objects.all())
    payment_method = serializers.ChoiceField(choices=Payment.PaymentMethod.choices)
    transaction_id = serializers.CharField(max_length=30)
    sender_number = serializers.CharField(max_length=11)
    amount = serializers.DecimalField(max_digits=7, decimal_places=2)

    def validate_sender_number(self, value):
        validate_bangladeshi_phone(value)
        return value

    def validate_transaction_id(self, value):
        validate_transaction_id(value)
        return value

    def validate_amount(self, value):
        validate_amount(value)
        return value

    def validate(self, attrs):
        user = self.context['request'].user
        payment_method = attrs.get('payment_method')
        transaction_id = attrs.get('transaction_id')
        batch = attrs.get('batch')

        # Check duplicate transaction_id per payment_method
        if payment_method and transaction_id:
            if Payment.objects.filter(
                payment_method=payment_method,
                transaction_id=transaction_id,
            ).exists():
                raise serializers.ValidationError(
                    {'transaction_id': 'This transaction ID is already used for this payment method.'}
                )

        # Check if user is already enrolled in this batch
        if batch:
            if Enrollment.objects.filter(user=user, batch=batch).exists():
                raise serializers.ValidationError(
                    {'batch': 'You are already enrolled in this batch.'}
                )

        return attrs


class PaymentListSerializer(serializers.ModelSerializer):
    """Serializer for listing a user's payment history."""

    batch_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id', 'batch', 'batch_name', 'payment_method', 'transaction_id',
            'sender_number', 'amount', 'status', 'admin_notes',
            'created_at', 'reviewed_at',
        ]

    def get_batch_name(self, obj):
        return obj.batch.name


class PaymentAdminSerializer(serializers.ModelSerializer):
    """Serializer for admin payment review."""

    user_username = serializers.SerializerMethodField()
    batch_name = serializers.SerializerMethodField()

    class Meta:
        model = Payment
        fields = [
            'id', 'user', 'user_username', 'batch', 'batch_name',
            'payment_method', 'transaction_id', 'sender_number', 'amount',
            'status', 'admin_notes', 'created_at', 'reviewed_at',
        ]
        read_only_fields = [
            'id', 'user', 'user_username', 'batch', 'batch_name',
            'payment_method', 'transaction_id', 'sender_number', 'amount',
            'status', 'created_at', 'reviewed_at',
        ]

    def get_user_username(self, obj):
        return obj.user.username

    def get_batch_name(self, obj):
        return obj.batch.name


# =============================================================================
# Exam Serializers
# =============================================================================


class ExamListSerializer(serializers.ModelSerializer):
    """Serializer for exam listing with unlock status and question count."""

    question_count = serializers.SerializerMethodField()
    is_unlocked = serializers.SerializerMethodField()

    class Meta:
        model = Exam
        fields = [
            'id', 'batch', 'title', 'duration_minutes', 'order',
            'passing_score', 'is_active', 'unlock_datetime',
            'question_count', 'is_unlocked',
        ]

    def get_question_count(self, obj):
        return obj.questions.count()

    def get_is_unlocked(self, obj):
        # Check if is_unlocked was annotated/set on the instance
        if hasattr(obj, 'is_unlocked_flag'):
            return obj.is_unlocked_flag
        # Fall back to context
        return self.context.get('is_unlocked', {}).get(obj.id, False)


class ExamDetailSerializer(serializers.ModelSerializer):
    """Serializer for exam detail view (admin)."""

    question_count = serializers.SerializerMethodField()

    class Meta:
        model = Exam
        fields = [
            'id', 'batch', 'title', 'duration_minutes', 'order',
            'passing_score', 'is_active', 'unlock_datetime',
            'created_at', 'updated_at', 'question_count',
        ]

    def get_question_count(self, obj):
        return obj.questions.count()


class ExamCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating and updating exams with validation."""

    class Meta:
        model = Exam
        fields = [
            'batch', 'title', 'duration_minutes', 'order',
            'passing_score', 'is_active', 'unlock_datetime',
        ]

    def validate_title(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field is required.")
        if len(value) > 200:
            raise serializers.ValidationError(
                "Ensure this field has no more than 200 characters."
            )
        return value

    def validate_duration_minutes(self, value):
        if value is None:
            raise serializers.ValidationError("This field is required.")
        if value < 1:
            raise serializers.ValidationError(
                "Duration must be at least 1 minute."
            )
        if value > 300:
            raise serializers.ValidationError(
                "Duration must be no more than 300 minutes."
            )
        return value

    def validate_order(self, value):
        if value is None:
            raise serializers.ValidationError("This field is required.")
        if value < 1:
            raise serializers.ValidationError(
                "Order must be a positive integer."
            )
        return value

    def validate_passing_score(self, value):
        if value is None:
            raise serializers.ValidationError("This field is required.")
        if value < 0:
            raise serializers.ValidationError(
                "Passing score must be at least 0."
            )
        if value > 100:
            raise serializers.ValidationError(
                "Passing score must be no more than 100."
            )
        return value


class ExamQuestionSerializer(serializers.ModelSerializer):
    """Serializer for exam taking - excludes correct_answer and explanation."""

    class Meta:
        model = ExamQuestion
        fields = ['id', 'question_text', 'answer_1', 'answer_2', 'answer_3', 'answer_4']


class ExamQuestionAdminSerializer(serializers.ModelSerializer):
    """Serializer for admin - includes correct_answer and explanation."""

    class Meta:
        model = ExamQuestion
        fields = [
            'id', 'exam', 'question_text', 'answer_1', 'answer_2',
            'answer_3', 'answer_4', 'correct_answer', 'explanation',
            'created_at',
        ]


# =============================================================================
# ExamAttempt Serializers
# =============================================================================


class ExamAttemptStartSerializer(serializers.Serializer):
    """Read-only response serializer for when a user starts an exam attempt."""

    attempt_id = serializers.IntegerField()
    questions = ExamQuestionSerializer(many=True)


class ExamSubmissionSerializer(serializers.Serializer):
    """Serializer for accepting a single exam submission."""

    question_id = serializers.IntegerField()
    selected_answer = serializers.IntegerField(allow_null=True, required=False)

    def validate_selected_answer(self, value):
        if value is not None and value not in (1, 2, 3, 4):
            raise serializers.ValidationError(
                "Selected answer must be between 1 and 4, or null."
            )
        return value


class ExamAttemptResultSerializer(serializers.ModelSerializer):
    """Serializer for returning exam attempt results with scoring details."""

    class Meta:
        model = ExamAttempt
        fields = [
            'id', 'exam', 'score', 'total_questions', 'correct_count',
            'incorrect_count', 'unanswered_count', 'start_time', 'end_time',
            'is_completed',
        ]


# =============================================================================
# Leaderboard Serializers
# =============================================================================


class BatchLeaderboardEntrySerializer(serializers.Serializer):
    """Serializer for a single entry in the batch leaderboard."""

    rank = serializers.IntegerField()
    user_display_name = serializers.CharField()
    total_score = serializers.FloatField()
    total_exams_completed = serializers.IntegerField()
    average_score = serializers.FloatField()


class ExamLeaderboardEntrySerializer(serializers.Serializer):
    """Serializer for a single entry in the exam leaderboard."""

    rank = serializers.IntegerField()
    user_display_name = serializers.CharField()
    score = serializers.FloatField()
    correct_count = serializers.IntegerField()
    incorrect_count = serializers.IntegerField()
    duration_seconds = serializers.FloatField()


class LeaderboardResponseSerializer(serializers.Serializer):
    """Serializer for the paginated leaderboard response with requesting user's entry."""

    entries = serializers.ListField()
    total_count = serializers.IntegerField()
    current_user_entry = serializers.DictField(allow_null=True)


# =============================================================================
# Category Admin Serializers (admissionlife-question-bank-management)
# =============================================================================


class CategoryAdminSerializer(serializers.ModelSerializer):
    """Serializer for admin category CRUD operations.

    Accepts name, parent, and order. Auto-calculates level based on parent.
    Validates (name, parent) uniqueness.
    """

    class Meta:
        model = Category
        fields = ['id', 'name', 'parent', 'order', 'level']
        read_only_fields = ['id', 'level']

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field is required.")
        if len(value) > 100:
            raise serializers.ValidationError(
                "Ensure this field has no more than 100 characters."
            )
        return value

    def validate(self, attrs):
        name = attrs.get('name', getattr(self.instance, 'name', None))
        parent = attrs.get('parent', getattr(self.instance, 'parent', None))

        # Check (name, parent) uniqueness
        qs = Category.objects.filter(name=name, parent=parent)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                {"name": "A category with this name already exists under the same parent."}
            )

        return attrs

    def create(self, validated_data):
        # Level is auto-calculated by the model's save() method
        instance = Category(**validated_data)
        instance.save()
        return instance

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        # Level is recalculated by the model's save() method
        instance.save()
        return instance


# =============================================================================
# Category Serializers (admissionlife-questions)
# =============================================================================


# =============================================================================
# Admin Question Bank Management Serializers
# =============================================================================


class AnswerWriteSerializer(serializers.Serializer):
    """Serializer for nested answer objects in question create/update."""

    text = serializers.CharField(max_length=255)
    image = serializers.ImageField(required=False, allow_null=True)
    is_correct = serializers.BooleanField()

    def validate_text(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value


class QuestionAdminCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for admin question create/update with nested answers and labels."""

    answers = AnswerWriteSerializer(many=True)
    labels = serializers.PrimaryKeyRelatedField(
        queryset=Label.objects.all(),
        many=True,
        required=False,
    )
    category = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Question
        fields = [
            'question_text', 'question_image', 'explanation',
            'explanation_image', 'category', 'labels', 'answers',
        ]

    def validate_question_text(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def validate_answers(self, value):
        if not value or len(value) == 0:
            raise serializers.ValidationError("At least one answer is required.")
        return value


# =============================================================================
# Category Serializers (admissionlife-questions)
# =============================================================================


class CategorySerializer(serializers.ModelSerializer):
    """Serializer for flat category listing."""

    class Meta:
        model = Category
        fields = ['id', 'name', 'parent', 'level', 'order']


class CategoryTreeSerializer(serializers.Serializer):
    """Recursive serializer for hierarchical category tree."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    level = serializers.IntegerField()
    order = serializers.IntegerField()
    children = serializers.SerializerMethodField()

    def get_children(self, obj):
        children = obj.get('children', [])
        return CategoryTreeSerializer(children, many=True).data


# =============================================================================
# Question Serializers (admissionlife-questions)
# =============================================================================


class AnswerSerializer(serializers.ModelSerializer):
    """Serializer for answers including correctness."""

    class Meta:
        model = Answer
        fields = ['id', 'text', 'image', 'is_correct']


class AnswerWithoutCorrectSerializer(serializers.ModelSerializer):
    """For quiz-taking — hides is_correct."""

    class Meta:
        model = Answer
        fields = ['id', 'text', 'image']


class LabelSerializer(serializers.ModelSerializer):
    """Serializer for question labels."""

    class Meta:
        model = Label
        fields = ['id', 'name']


class QuestionDetailSerializer(serializers.ModelSerializer):
    """Full question detail with nested answers, labels, and category name."""

    answers = AnswerSerializer(many=True, read_only=True)
    labels = LabelSerializer(many=True, read_only=True)
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)

    class Meta:
        model = Question
        fields = [
            'id', 'question_text', 'question_image', 'explanation',
            'explanation_image', 'category', 'category_name', 'answers', 'labels',
        ]


class QuestionQuizSerializer(serializers.ModelSerializer):
    """For quiz-taking — hides correct answers and explanation."""

    answers = AnswerWithoutCorrectSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ['id', 'question_text', 'question_image', 'answers']


# =============================================================================
# Saved Question Serializers (admissionlife-questions)
# =============================================================================


class SavedQuestionCreateSerializer(serializers.ModelSerializer):
    """Serializer for saving/bookmarking a question. Uses get_or_create for idempotence."""

    question = serializers.PrimaryKeyRelatedField(queryset=Question.objects.all())

    class Meta:
        model = SavedQuestion
        fields = ['question']

    def create(self, validated_data):
        user = validated_data.get('user')
        guest_user = validated_data.get('guest_user')
        question = validated_data['question']

        if guest_user:
            saved, created = SavedQuestion.objects.get_or_create(
                guest_user=guest_user, question=question,
                defaults={'user': None}
            )
        else:
            saved, created = SavedQuestion.objects.get_or_create(
                user=user, question=question,
                defaults={'guest_user': None}
            )
        return saved


class SavedQuestionListSerializer(serializers.ModelSerializer):
    """Serializer for listing saved questions with full question detail."""

    question = QuestionDetailSerializer(read_only=True)

    class Meta:
        model = SavedQuestion
        fields = ['id', 'question', 'saved_at']


# =============================================================================
# Question Report Serializers (admissionlife-questions)
# =============================================================================


class QuestionReportCreateSerializer(serializers.ModelSerializer):
    """Serializer for reporting a problematic question."""

    class Meta:
        model = QuestionReport
        fields = ['question', 'reason']

    def validate_reason(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("This field is required.")
        return value


# =============================================================================
# Practice Quiz Serializers (admissionlife-questions)
# =============================================================================


class CategorySelectionSerializer(serializers.Serializer):
    """Serializer for a single category selection in practice quiz config."""

    category_id = serializers.IntegerField()
    question_count = serializers.IntegerField(min_value=1)
    include_subcategories = serializers.BooleanField(default=False)


class PracticeQuizConfigSerializer(serializers.Serializer):
    """Serializer for practice quiz generation request body."""

    categories = CategorySelectionSerializer(many=True)

    def validate_categories(self, value):
        if not value:
            raise serializers.ValidationError("At least one category selection is required.")
        return value


class PracticeQuizResponseSerializer(serializers.ModelSerializer):
    """Serializer for the generated practice quiz response."""

    questions = QuestionQuizSerializer(many=True, read_only=True)

    class Meta:
        model = Quiz
        fields = ['id', 'name', 'quiz_type', 'duration_minutes', 'questions']


class PracticeQuizAttemptStartSerializer(serializers.Serializer):
    """Read-only response serializer for when a user starts a practice quiz attempt."""

    attempt_id = serializers.IntegerField()
    questions = QuestionQuizSerializer(many=True)


class PracticeQuizSubmissionSerializer(serializers.Serializer):
    """Serializer for accepting a single practice quiz answer submission."""

    question_id = serializers.IntegerField()
    selected_answer_id = serializers.IntegerField(allow_null=True, required=False)


class PracticeQuizSubmissionDetailSerializer(serializers.ModelSerializer):
    """Serializer for per-question submission detail in quiz results."""

    question = QuestionDetailSerializer(read_only=True)
    selected_answer = AnswerSerializer(read_only=True)
    correct_answer = serializers.SerializerMethodField()

    class Meta:
        model = UserSubmission
        fields = ['question', 'selected_answer', 'correct_answer', 'is_correct']

    def get_correct_answer(self, obj):
        correct = obj.question.answers.filter(is_correct=True).first()
        return AnswerSerializer(correct).data if correct else None


class PracticeQuizAttemptResultSerializer(serializers.ModelSerializer):
    """Serializer for returning practice quiz attempt results with submission details."""

    submissions = serializers.SerializerMethodField()

    class Meta:
        model = QuizAttempt
        fields = ['id', 'quiz', 'score', 'is_completed', 'start_time', 'end_time', 'submissions']

    def get_submissions(self, obj):
        submissions = UserSubmission.objects.filter(attempt=obj).select_related(
            'question', 'selected_answer'
        ).prefetch_related('question__answers', 'question__labels')
        return PracticeQuizSubmissionDetailSerializer(submissions, many=True).data
