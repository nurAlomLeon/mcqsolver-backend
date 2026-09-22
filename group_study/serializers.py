from django.contrib.auth import get_user_model
from rest_framework import serializers

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
        fields = ['id', 'username', 'first_name', 'last_name', 'email']


class StudyGroupWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudyGroup
        fields = [
            'id',
            'name',
            'description',
            'is_active',
            'is_public',
            'members_can_create_quizzes',
        ]
        read_only_fields = ['id']


class StudyGroupMembershipSerializer(serializers.ModelSerializer):
    user = UserSummarySerializer(read_only=True)

    class Meta:
        model = StudyGroupMembership
        fields = ['id', 'user', 'role', 'joined_at']


class StudyGroupSerializer(serializers.ModelSerializer):
    created_by = UserSummarySerializer(read_only=True)
    member_count = serializers.IntegerField(read_only=True)
    quiz_count = serializers.IntegerField(read_only=True)
    unread_message_count = serializers.IntegerField(read_only=True, default=0)
    my_role = serializers.SerializerMethodField()
    my_notify_messages = serializers.SerializerMethodField()

    class Meta:
        model = StudyGroup
        fields = [
            'id',
            'name',
            'description',
            'is_active',
            'is_public',
            'members_can_create_quizzes',
            'created_by',
            'member_count',
            'quiz_count',
            'unread_message_count',
            'my_role',
            'my_notify_messages',
            'created_at',
            'updated_at',
        ]

    def get_my_role(self, obj):
        return getattr(obj, 'my_role', None)

    def get_my_notify_messages(self, obj):
        value = getattr(obj, 'my_notify_messages', None)
        return True if value is None else bool(value)


class StudyGroupDetailSerializer(StudyGroupSerializer):
    """Group detail with a small member preview.

    Large groups are listed page by page through the members endpoint, so the
    detail payload stays small no matter how many people joined.
    """

    members = serializers.SerializerMethodField()

    class Meta(StudyGroupSerializer.Meta):
        fields = StudyGroupSerializer.Meta.fields + ['members']

    def get_members(self, obj):
        preview = getattr(obj, 'member_preview', None)
        if preview is None:
            preview = obj.memberships.select_related('user').order_by('joined_at', 'id')[:50]
        return StudyGroupMembershipSerializer(preview, many=True).data


class PublicGroupSerializer(StudyGroupSerializer):
    """Discover-list item that also tells whether the caller already joined."""

    is_member = serializers.SerializerMethodField()

    class Meta(StudyGroupSerializer.Meta):
        fields = StudyGroupSerializer.Meta.fields + ['is_member']

    def get_is_member(self, obj):
        return bool(getattr(obj, 'is_member', False))


class GroupStudyAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupStudyAnswer
        fields = ['id', 'position', 'text', 'is_correct']


class CandidateGroupStudyAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupStudyAnswer
        fields = ['id', 'position', 'text']


class GroupStudyQuestionSerializer(serializers.ModelSerializer):
    answers = GroupStudyAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = GroupStudyQuestion
        fields = ['id', 'position', 'question_text', 'explanation', 'answers']


class CandidateGroupStudyQuestionSerializer(serializers.ModelSerializer):
    answers = CandidateGroupStudyAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = GroupStudyQuestion
        fields = ['id', 'position', 'question_text', 'answers']


class GroupStudyQuizSummarySerializer(serializers.ModelSerializer):
    question_count = serializers.SerializerMethodField()
    correct_mark = serializers.SerializerMethodField()
    wrong_mark = serializers.SerializerMethodField()
    unanswered_mark = serializers.SerializerMethodField()
    maximum_score = serializers.SerializerMethodField()
    attempt_status = serializers.SerializerMethodField()
    created_by = UserSummarySerializer(read_only=True)

    class Meta:
        model = GroupStudyQuiz
        fields = [
            'id',
            'name',
            'description',
            'start_at',
            'end_at',
            'duration_minutes',
            'correct_mark',
            'wrong_mark',
            'unanswered_mark',
            'is_published',
            'question_count',
            'maximum_score',
            'attempt_status',
            'created_by',
            'created_at',
        ]

    def get_question_count(self, obj):
        question_count = getattr(obj, 'question_count', None)
        if question_count is not None:
            return question_count
        prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('questions')
        if prefetched is not None:
            return len(prefetched)
        return obj.questions.count()

    def get_correct_mark(self, obj):
        return decimal_as_number(obj.correct_mark)

    def get_wrong_mark(self, obj):
        return decimal_as_number(obj.wrong_mark)

    def get_unanswered_mark(self, obj):
        return decimal_as_number(obj.unanswered_mark)

    def get_maximum_score(self, obj):
        return decimal_as_number(self.get_question_count(obj) * obj.correct_mark)

    def get_attempt_status(self, obj):
        if getattr(obj, 'has_in_progress_attempt', False):
            return 'IN_PROGRESS'
        if getattr(obj, 'completed_attempt_count', 0) > 0:
            return 'COMPLETED'
        return 'NOT_STARTED'


class GroupStudyQuizDetailSerializer(GroupStudyQuizSummarySerializer):
    questions = GroupStudyQuestionSerializer(many=True, read_only=True)

    class Meta(GroupStudyQuizSummarySerializer.Meta):
        fields = GroupStudyQuizSummarySerializer.Meta.fields + ['questions']


class CandidateGroupStudyQuizDetailSerializer(GroupStudyQuizSummarySerializer):
    questions = CandidateGroupStudyQuestionSerializer(many=True, read_only=True)

    class Meta(GroupStudyQuizSummarySerializer.Meta):
        fields = GroupStudyQuizSummarySerializer.Meta.fields + ['questions']


class GroupStudyAnswerWriteSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=1, required=False)
    text = serializers.CharField(max_length=255)
    is_correct = serializers.BooleanField()


class GroupStudyQuestionWriteSerializer(serializers.Serializer):
    position = serializers.IntegerField(min_value=1, required=False)
    question_text = serializers.CharField()
    explanation = serializers.CharField(required=False, allow_blank=True, default='')
    answers = GroupStudyAnswerWriteSerializer(many=True, allow_empty=False)


class GroupStudyQuizWriteSerializer(serializers.ModelSerializer):
    question_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        write_only=True,
    )
    source_quiz_id = serializers.IntegerField(required=False, write_only=True)
    questions = GroupStudyQuestionWriteSerializer(many=True, required=False, write_only=True)

    class Meta:
        model = GroupStudyQuiz
        fields = [
            'id',
            'name',
            'description',
            'start_at',
            'end_at',
            'duration_minutes',
            'correct_mark',
            'wrong_mark',
            'unanswered_mark',
            'is_published',
            'question_ids',
            'source_quiz_id',
            'questions',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        start_at = attrs.get('start_at', getattr(self.instance, 'start_at', None))
        end_at = attrs.get('end_at', getattr(self.instance, 'end_at', None))
        if start_at and end_at and end_at <= start_at:
            raise serializers.ValidationError({'end_at': 'End time must be after start time.'})

        provided = []
        if attrs.get('questions'):
            provided.append('questions')
        if attrs.get('question_ids'):
            provided.append('question_ids')
        if attrs.get('source_quiz_id'):
            provided.append('source_quiz_id')
        if len(provided) > 1:
            raise serializers.ValidationError(
                'Provide at most one of questions, question_ids, or source_quiz_id.'
            )
        return attrs


class GroupStudyQuizAddQuestionsSerializer(serializers.Serializer):
    question_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
    )
    source_quiz_id = serializers.IntegerField(required=False)
    questions = GroupStudyQuestionWriteSerializer(many=True, required=False)

    def validate(self, attrs):
        provided = []
        if attrs.get('questions'):
            provided.append('questions')
        if attrs.get('question_ids'):
            provided.append('question_ids')
        if attrs.get('source_quiz_id'):
            provided.append('source_quiz_id')
        if len(provided) != 1:
            raise serializers.ValidationError(
                'Provide exactly one of questions, question_ids, or source_quiz_id.'
            )
        return attrs


class GroupStudyQuizPublishSerializer(serializers.Serializer):
    pass


class GroupStudyMemberAddSerializer(serializers.Serializer):
    emails = serializers.ListField(
        child=serializers.EmailField(),
        allow_empty=False,
    )


class BulkGroupSubmissionItemSerializer(serializers.Serializer):
    question_id = serializers.IntegerField(min_value=1)
    selected_answer_id = serializers.IntegerField(required=False, allow_null=True, default=None, min_value=1)


class BulkGroupSubmissionSerializer(serializers.Serializer):
    submissions = BulkGroupSubmissionItemSerializer(many=True, allow_empty=False)


class GroupStudyQuizAttemptStartSerializer(serializers.ModelSerializer):
    quiz = CandidateGroupStudyQuizDetailSerializer(read_only=True)
    score = serializers.SerializerMethodField()

    class Meta:
        model = GroupStudyQuizAttempt
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


class GroupStudyResultAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupStudyAnswer
        fields = ['id', 'position', 'text', 'is_correct']


class GroupStudyResultQuestionSerializer(serializers.ModelSerializer):
    answers = GroupStudyResultAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = GroupStudyQuestion
        fields = ['id', 'position', 'question_text', 'explanation', 'answers']


class GroupStudyResultSubmissionSerializer(serializers.ModelSerializer):
    question = GroupStudyResultQuestionSerializer(read_only=True)
    selected_answer_id = serializers.IntegerField(source='selected_answer.id', allow_null=True)

    class Meta:
        model = GroupStudyQuizSubmission
        fields = ['question', 'selected_answer_id', 'is_correct']


class GroupStudyQuizAttemptResultSerializer(serializers.ModelSerializer):
    quiz = GroupStudyQuizDetailSerializer(read_only=True)
    submissions = GroupStudyResultSubmissionSerializer(many=True, read_only=True)
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
        model = GroupStudyQuizAttempt
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

    def get_correct_mark(self, obj):
        return decimal_as_number(obj.correct_mark)

    def get_wrong_mark(self, obj):
        return decimal_as_number(obj.wrong_mark)

    def get_unanswered_mark(self, obj):
        return decimal_as_number(obj.unanswered_mark)

    def _stats(self, obj):
        prefetched = getattr(obj, '_prefetched_objects_cache', {}).get('submissions')
        submissions = list(prefetched) if prefetched is not None else list(obj.submissions.all())
        attempted_count = sum(1 for s in submissions if s.selected_answer_id is not None)
        correct_count = sum(1 for s in submissions if s.is_correct)
        wrong_count = sum(
            1 for s in submissions
            if s.selected_answer_id is not None and not s.is_correct
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
        if stats['attempted_count'] == 0:
            return 0.0
        return round((stats['correct_count'] / stats['attempted_count']) * 100, 2)

    def get_attempted_string(self, obj):
        stats = self._stats(obj)
        return f"{stats['attempted_count']}/{obj.total_questions}"

    def get_maximum_score(self, obj):
        return decimal_as_number(obj.total_questions * obj.correct_mark)


class GroupMessageSerializer(serializers.ModelSerializer):
    sender = UserSummarySerializer(read_only=True)

    class Meta:
        model = GroupMessage
        fields = ['id', 'group', 'sender', 'body', 'created_at']
        read_only_fields = ['id', 'group', 'sender', 'created_at']


class GroupMessageWriteSerializer(serializers.Serializer):
    body = serializers.CharField()


class GroupMessageNotificationSerializer(serializers.Serializer):
    notify_messages = serializers.BooleanField()
