from rest_framework import serializers

from api.models import Answer

from .models import (
    DailyMcqDelivery,
    DailyMcqSubscription,
    DeviceInstallation,
    Notification,
)


class DeviceInstallationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceInstallation
        fields = ['id', 'platform', 'is_active', 'created_at', 'updated_at']
        read_only_fields = fields


class RegisterDeviceInstallationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255)
    platform = serializers.ChoiceField(
        choices=DeviceInstallation.Platform.choices,
        default=DeviceInstallation.Platform.ANDROID,
    )


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = [
            'id', 'kind', 'title', 'body', 'route', 'payload',
            'is_read', 'read_at', 'created_at',
        ]
        read_only_fields = fields


class McqAnswerOptionSerializer(serializers.ModelSerializer):
    """Answer option. Correctness is only exposed once the user has answered."""

    class Meta:
        model = Answer
        fields = ['id', 'text']
        read_only_fields = fields


class DailyMcqDeliverySerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source='question.question_text', read_only=True)
    answers = serializers.SerializerMethodField()
    correct_answer_id = serializers.SerializerMethodField()
    explanation = serializers.SerializerMethodField()
    is_answered = serializers.BooleanField(read_only=True)

    class Meta:
        model = DailyMcqDelivery
        fields = [
            'id', 'question_id', 'question_text', 'answers',
            'selected_answer', 'is_correct', 'is_answered', 'answered_at',
            'correct_answer_id', 'explanation',
            'delivered_date', 'slot',
        ]
        read_only_fields = fields

    def get_answers(self, obj):
        options = obj.question.answers.all()
        return McqAnswerOptionSerializer(options, many=True).data

    def get_correct_answer_id(self, obj):
        # Withhold the answer key until the user commits to a choice.
        if not obj.is_answered:
            return None
        correct = obj.question.answers.filter(is_correct=True).first()
        return correct.id if correct else None

    def get_explanation(self, obj):
        if not obj.is_answered:
            return None
        return obj.question.explanation or ''


class SubmitMcqAnswerSerializer(serializers.Serializer):
    answer_id = serializers.IntegerField()


class DailyMcqSubscriptionSerializer(serializers.ModelSerializer):
    max_questions_per_day = serializers.SerializerMethodField()

    class Meta:
        model = DailyMcqSubscription
        fields = [
            'is_active', 'questions_per_day', 'max_questions_per_day',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']

    def get_max_questions_per_day(self, _obj):
        return DailyMcqSubscription.MAX_QUESTIONS_PER_DAY

    def validate_questions_per_day(self, value):
        if not (
            DailyMcqSubscription.MIN_QUESTIONS_PER_DAY
            <= value
            <= DailyMcqSubscription.MAX_QUESTIONS_PER_DAY
        ):
            raise serializers.ValidationError(
                f'Choose between {DailyMcqSubscription.MIN_QUESTIONS_PER_DAY} and '
                f'{DailyMcqSubscription.MAX_QUESTIONS_PER_DAY} questions per day.'
            )
        return value
