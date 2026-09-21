from django.contrib.auth import get_user_model
from django.db.models import Count
from rest_framework import serializers

from .models import (
    Course,
    CourseAnswer,
    CourseEnrollment,
    CourseQuestion,
    CourseQuiz,
    CourseQuizAttempt,
    CourseQuizSubmission,
)
from .services import ensure_quiz_schedule_matches_course, validate_question_payloads


User = get_user_model()


def decimal_as_number(value):
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


class UserSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name']


class CourseAnswerWriteSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=1, required=False)
    text = serializers.CharField(max_length=255)
    is_correct = serializers.BooleanField()


class CourseQuestionWriteSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=1, required=False)
    question_text = serializers.CharField()
    explanation = serializers.CharField(required=False, allow_blank=True, default='')
    source_question_id = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    source_question_url = serializers.URLField(required=False, allow_blank=True, default='')
    answers = CourseAnswerWriteSerializer(many=True, allow_empty=False)


class CourseWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = [
            'id',
            'name',
            'description',
            'course_type',
            'is_published',
            'allow_self_enrollment',
        ]
        read_only_fields = ['id']


class CourseEnrollmentSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)
    enrolled_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = CourseEnrollment
        fields = [
            'id',
            'user',
            'is_active',
            'source',
            'enrolled_by',
            'enrolled_at',
            'updated_at',
        ]


class CourseEnrollmentWriteSerializer(serializers.Serializer):
    user_id = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='user',
        required=False,
    )
    email = serializers.EmailField(required=False)
    is_active = serializers.BooleanField(required=False, default=True)

    def validate(self, attrs):
        user = attrs.get('user')
        email = attrs.get('email')

        if bool(user) == bool(email):
            raise serializers.ValidationError(
                'Provide exactly one of user_id or email.'
            )

        if email:
            matched_users = list(User.objects.filter(email__iexact=email)[:2])
            if not matched_users:
                raise serializers.ValidationError({'email': ['No user found with this email.']})
            if len(matched_users) > 1:
                raise serializers.ValidationError(
                    {'email': ['Multiple users found with this email. Use user_id instead.']}
                )
            attrs['user'] = matched_users[0]

        return attrs


class CourseAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAnswer
        fields = ['id', 'position', 'text', 'is_correct']


class CandidateCourseAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAnswer
        fields = ['id', 'position', 'text']


class CourseQuestionSerializer(serializers.ModelSerializer):
    answers = CourseAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = CourseQuestion
        fields = [
            'id',
            'position',
            'question_text',
            'explanation',
            'source_question_id',
            'source_question_url',
            'answers',
        ]


class CandidateCourseQuestionSerializer(serializers.ModelSerializer):
    answers = CandidateCourseAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = CourseQuestion
        fields = ['id', 'position', 'question_text', 'answers']


class CourseQuizSummarySerializer(serializers.ModelSerializer):
    question_count = serializers.SerializerMethodField()
    correct_mark = serializers.SerializerMethodField()
    wrong_mark = serializers.SerializerMethodField()
    unanswered_mark = serializers.SerializerMethodField()
    maximum_score = serializers.SerializerMethodField()
    is_unlocked = serializers.SerializerMethodField()
    attempt_status = serializers.SerializerMethodField()
    completed_attempt_count = serializers.SerializerMethodField()

    class Meta:
        model = CourseQuiz
        fields = [
            'id',
            'name',
            'topic',
            'quiz_type',
            'position',
            'duration_minutes',
            'correct_mark',
            'wrong_mark',
            'unanswered_mark',
            'unlock_at',
            'is_active',
            'question_count',
            'maximum_score',
            'is_unlocked',
            'attempt_status',
            'completed_attempt_count',
        ]

    def get_maximum_score(self, obj):
        question_count = self.get_question_count(obj)
        return decimal_as_number(question_count * obj.correct_mark)

    def get_question_count(self, obj):
        question_count = getattr(obj, 'question_count', None)
        if question_count is not None:
            return question_count

        prefetched_questions = getattr(
            obj,
            '_prefetched_objects_cache',
            {},
        ).get('questions')
        if prefetched_questions is not None:
            return len(prefetched_questions)
        return obj.questions.count()

    def get_correct_mark(self, obj):
        return decimal_as_number(obj.correct_mark)

    def get_wrong_mark(self, obj):
        return decimal_as_number(obj.wrong_mark)

    def get_unanswered_mark(self, obj):
        return decimal_as_number(obj.unanswered_mark)

    def get_is_unlocked(self, obj):
        if self.context.get('is_staff_view', False):
            return True
        if obj.course.course_type == Course.CourseType.ARCHIVE:
            return True
        return bool(obj.unlock_at and obj.unlock_at <= self.context['now'])

    def get_attempt_status(self, obj):
        if getattr(obj, 'has_in_progress_attempt', False):
            return 'IN_PROGRESS'
        if self.get_completed_attempt_count(obj) > 0:
            return 'COMPLETED'
        return 'NOT_STARTED'

    def get_completed_attempt_count(self, obj):
        return getattr(obj, 'completed_attempt_count', 0)


class CourseQuizDetailSerializer(CourseQuizSummarySerializer):
    questions = CourseQuestionSerializer(many=True, read_only=True)

    class Meta(CourseQuizSummarySerializer.Meta):
        fields = CourseQuizSummarySerializer.Meta.fields + ['questions']


class CandidateCourseQuizDetailSerializer(CourseQuizSummarySerializer):
    questions = CandidateCourseQuestionSerializer(many=True, read_only=True)

    class Meta(CourseQuizSummarySerializer.Meta):
        fields = CourseQuizSummarySerializer.Meta.fields + ['questions']


class CourseQuizWriteSerializer(serializers.ModelSerializer):
    questions = CourseQuestionWriteSerializer(many=True, required=False)

    class Meta:
        model = CourseQuiz
        fields = [
            'id',
            'name',
            'topic',
            'quiz_type',
            'position',
            'duration_minutes',
            'correct_mark',
            'wrong_mark',
            'unanswered_mark',
            'unlock_at',
            'is_active',
            'questions',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        course = self.context['course']
        unlock_at = attrs.get('unlock_at', getattr(self.instance, 'unlock_at', None))
        ensure_quiz_schedule_matches_course(course, {'unlock_at': unlock_at})
        questions = attrs.get('questions')
        if questions is not None:
            validate_question_payloads(questions)
        return attrs


class StaffCourseSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    enrollment_count = serializers.IntegerField(read_only=True)
    quiz_count = serializers.IntegerField(read_only=True)
    is_enrolled = serializers.BooleanField(read_only=True)
    quizzes = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            'id',
            'name',
            'description',
            'course_type',
            'is_published',
            'allow_self_enrollment',
            'created_by',
            'created_at',
            'updated_at',
            'enrollment_count',
            'quiz_count',
            'is_enrolled',
            'quizzes',
        ]

    def get_quizzes(self, obj):
        quizzes = getattr(obj, 'prefetched_quizzes', None)
        if quizzes is None:
            quizzes = obj.quizzes.annotate(question_count=Count('questions')).prefetch_related('questions__answers')
        else:
            quizzes = list(quizzes)
        serializer = CourseQuizDetailSerializer(
            quizzes,
            many=True,
            context={'now': self.context['now'], 'is_staff_view': True},
        )
        return serializer.data


class LearnerCourseSerializer(serializers.ModelSerializer):
    quiz_count = serializers.IntegerField(read_only=True)
    is_enrolled = serializers.BooleanField(read_only=True)
    quizzes = serializers.SerializerMethodField()

    class Meta:
        model = Course
        fields = [
            'id',
            'name',
            'description',
            'course_type',
            'is_published',
            'allow_self_enrollment',
            'created_at',
            'updated_at',
            'quiz_count',
            'is_enrolled',
            'quizzes',
        ]

    def get_quizzes(self, obj):
        if not getattr(obj, 'is_enrolled', False):
            return []
        quizzes = getattr(obj, 'prefetched_quizzes', None)
        if quizzes is None:
            quizzes = obj.quizzes.filter(is_active=True).annotate(question_count=Count('questions'))
        else:
            quizzes = [quiz for quiz in quizzes if quiz.is_active]
        serializer = CourseQuizSummarySerializer(
            quizzes,
            many=True,
            context={'now': self.context['now'], 'is_staff_view': False},
        )
        return serializer.data


class CourseHomeCardSerializer(serializers.ModelSerializer):
    """Slim course payload for the home screen sliders.

    Excludes the nested quizzes list that ``LearnerCourseSerializer`` carries so
    the home screen stays cheap to load. ``quiz_count`` counts active quizzes
    only and ``completed_quiz_count`` is the requesting user's progress.
    """

    quiz_count = serializers.IntegerField(read_only=True)
    enrollment_count = serializers.IntegerField(read_only=True)
    completed_quiz_count = serializers.IntegerField(read_only=True)
    is_enrolled = serializers.BooleanField(read_only=True)

    class Meta:
        model = Course
        fields = [
            'id',
            'name',
            'description',
            'course_type',
            'allow_self_enrollment',
            'quiz_count',
            'enrollment_count',
            'completed_quiz_count',
            'is_enrolled',
        ]


class CourseHomeExamSerializer(CourseQuizSummarySerializer):
    """A course exam plus enough course context to render it standalone."""

    course_id = serializers.IntegerField(source='course.id', read_only=True)
    course_name = serializers.CharField(source='course.name', read_only=True)
    course_type = serializers.CharField(
        source='course.course_type',
        read_only=True,
    )

    class Meta(CourseQuizSummarySerializer.Meta):
        fields = CourseQuizSummarySerializer.Meta.fields + [
            'course_id',
            'course_name',
            'course_type',
        ]


class AnswersheetImportQuestionSerializer(serializers.Serializer):
    source_question_id = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    source_question_url = serializers.URLField(required=False, allow_blank=True, default='')
    question_text = serializers.CharField()
    option_a = serializers.CharField(max_length=255)
    option_b = serializers.CharField(max_length=255)
    option_c = serializers.CharField(max_length=255)
    option_d = serializers.CharField(max_length=255)
    correct_option = serializers.ChoiceField(choices=['A', 'B', 'C', 'D'])
    explanation = serializers.CharField(required=False, allow_blank=True, default='')


class CourseQuizImportSerializer(serializers.Serializer):
    quiz_name = serializers.CharField(max_length=200)
    topic = serializers.CharField(required=False, allow_blank=True, default='')
    quiz_type = serializers.ChoiceField(
        choices=CourseQuiz.QuizType.choices,
        required=False,
        default=CourseQuiz.QuizType.QUESTION_BANK,
    )
    duration_minutes = serializers.IntegerField(min_value=1)
    correct_mark = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default='1.00')
    wrong_mark = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default='0')
    unanswered_mark = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, default='0')
    unlock_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    source_name = serializers.CharField(required=False, allow_blank=True, max_length=50, default='')
    external_quiz_id = serializers.CharField(required=False, allow_blank=True, max_length=100, default='')
    source_url = serializers.URLField(required=False, allow_blank=True, default='')
    is_active = serializers.BooleanField(required=False, default=True)
    questions = AnswersheetImportQuestionSerializer(many=True, allow_empty=False)


class BulkCourseQuizSubmissionItemSerializer(serializers.Serializer):
    question_id = serializers.IntegerField(min_value=1)
    selected_answer_id = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=1)


class BulkCourseQuizSubmissionSerializer(serializers.Serializer):
    submissions = BulkCourseQuizSubmissionItemSerializer(many=True, allow_empty=False)


class CourseQuizAttemptStartSerializer(serializers.ModelSerializer):
    quiz = CandidateCourseQuizDetailSerializer(read_only=True)
    score = serializers.SerializerMethodField()

    class Meta:
        model = CourseQuizAttempt
        fields = [
            'id',
            'quiz',
            'score',
            'is_completed',
            'start_time',
            'expires_at',
            'end_time',
            'total_questions',
        ]

    def get_score(self, obj):
        return decimal_as_number(obj.score)

    def to_representation(self, instance):
        instance.quiz.completed_attempt_count = getattr(
            instance,
            'quiz_completed_attempt_count',
            getattr(instance.quiz, 'completed_attempt_count', 0),
        )
        instance.quiz.has_in_progress_attempt = getattr(
            instance,
            'quiz_has_in_progress_attempt',
            getattr(instance.quiz, 'has_in_progress_attempt', False),
        )
        return super().to_representation(instance)


class CourseQuizResultAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseAnswer
        fields = ['id', 'position', 'text', 'is_correct']


class CourseQuizResultQuestionSerializer(serializers.ModelSerializer):
    answers = CourseQuizResultAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = CourseQuestion
        fields = ['id', 'position', 'question_text', 'explanation', 'answers']


class CourseQuizResultSubmissionSerializer(serializers.ModelSerializer):
    question = CourseQuizResultQuestionSerializer(read_only=True)
    selected_answer_id = serializers.IntegerField(source='selected_answer.id', allow_null=True)

    class Meta:
        model = CourseQuizSubmission
        fields = ['question', 'selected_answer_id', 'is_correct']


class CourseQuizAttemptResultSerializer(serializers.ModelSerializer):
    quiz = CourseQuizDetailSerializer(read_only=True)
    submissions = CourseQuizResultSubmissionSerializer(many=True, read_only=True)
    score = serializers.SerializerMethodField()
    correct_answers = serializers.SerializerMethodField()
    wrong_answers = serializers.SerializerMethodField()
    unanswered = serializers.SerializerMethodField()
    accuracy = serializers.SerializerMethodField()
    attempted_string = serializers.SerializerMethodField()
    maximum_score = serializers.SerializerMethodField()
    correct_mark = serializers.SerializerMethodField()
    wrong_mark = serializers.SerializerMethodField()
    unanswered_mark = serializers.SerializerMethodField()

    class Meta:
        model = CourseQuizAttempt
        fields = [
            'id',
            'quiz',
            'score',
            'correct_answers',
            'wrong_answers',
            'unanswered',
            'accuracy',
            'attempted_string',
            'maximum_score',
            'is_completed',
            'start_time',
            'expires_at',
            'end_time',
            'total_questions',
            'correct_mark',
            'wrong_mark',
            'unanswered_mark',
            'submissions',
        ]

    def get_score(self, obj):
        return decimal_as_number(obj.score)

    def to_representation(self, instance):
        instance.quiz.completed_attempt_count = getattr(
            instance,
            'quiz_completed_attempt_count',
            getattr(instance.quiz, 'completed_attempt_count', 0),
        )
        instance.quiz.has_in_progress_attempt = getattr(
            instance,
            'quiz_has_in_progress_attempt',
            getattr(instance.quiz, 'has_in_progress_attempt', False),
        )
        return super().to_representation(instance)

    def get_correct_mark(self, obj):
        return decimal_as_number(obj.correct_mark)

    def get_wrong_mark(self, obj):
        return decimal_as_number(obj.wrong_mark)

    def get_unanswered_mark(self, obj):
        return decimal_as_number(obj.unanswered_mark)

    def _stats(self, obj):
        prefetched_submissions = getattr(obj, '_prefetched_objects_cache', {}).get('submissions')
        submissions = list(prefetched_submissions) if prefetched_submissions is not None else list(obj.submissions.all())
        attempted_count = sum(1 for submission in submissions if submission.selected_answer_id is not None)
        correct_count = sum(1 for submission in submissions if submission.is_correct)
        wrong_count = sum(
            1 for submission in submissions
            if submission.selected_answer_id is not None and not submission.is_correct
        )
        unanswered_count = max(obj.total_questions - attempted_count, 0)
        return {
            'attempted_count': attempted_count,
            'correct_count': correct_count,
            'wrong_count': wrong_count,
            'unanswered_count': unanswered_count,
        }

    def get_correct_answers(self, obj):
        return self._stats(obj)['correct_count']

    def get_wrong_answers(self, obj):
        return self._stats(obj)['wrong_count']

    def get_unanswered(self, obj):
        return self._stats(obj)['unanswered_count']

    def get_accuracy(self, obj):
        stats = self._stats(obj)
        attempted_count = stats['attempted_count']
        if attempted_count == 0:
            return 0.0
        return round((stats['correct_count'] / attempted_count) * 100, 2)

    def get_attempted_string(self, obj):
        stats = self._stats(obj)
        return f"{stats['attempted_count']}/{obj.total_questions}"

    def get_maximum_score(self, obj):
        return decimal_as_number(obj.total_questions * obj.correct_mark)
