from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from api.authentication import FlexibleAuthentication
from api.permissions import IsAuthenticatedOrGuest

from .mcq_stats import monthly_leaderboard, personal_stats
from .models import (
    DailyMcqDelivery,
    DailyMcqSubscription,
    DeviceInstallation,
    Notification,
    NotificationAutomation,
    NotificationKind,
)
from .serializers import (
    DailyMcqDeliverySerializer,
    DailyMcqSubscriptionSerializer,
    DeviceInstallationSerializer,
    NotificationSerializer,
    RegisterDeviceInstallationSerializer,
    SubmitMcqAnswerSerializer,
)


def owner_filter(request):
    """Build the queryset filter for the current user or guest."""
    if getattr(request.user, 'is_guest', False):
        return {'guest_user': request.user.guest_user}
    return {'user': request.user}


class DeviceInstallationView(APIView):
    """Register or refresh an FCM token for the current user or guest.

    Supports both regular Django token authentication and the app's
    existing guest authentication headers (``X-Guest-Token``/``X-Device-ID``)
    via ``FlexibleAuthentication``.
    """

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request):
        serializer = RegisterDeviceInstallationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        token = serializer.validated_data['token']
        platform = serializer.validated_data['platform']

        is_guest = getattr(request.user, 'is_guest', False)
        owner_kwargs = (
            {'guest_user': request.user.guest_user}
            if is_guest
            else {'user': request.user}
        )

        installation, _created = DeviceInstallation.objects.update_or_create(
            token=token,
            defaults={
                'platform': platform,
                'is_active': True,
                'user': owner_kwargs.get('user'),
                'guest_user': owner_kwargs.get('guest_user'),
            },
        )

        return Response(
            DeviceInstallationSerializer(installation).data,
            status=status.HTTP_201_CREATED,
        )


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(ListAPIView):
    """Paginated in-app notification history for the current user or guest."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = NotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return Notification.objects.filter(**owner_filter(self.request))


class NotificationUnreadCountView(APIView):
    """Unread badge count for the app bar."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request):
        count = Notification.objects.filter(
            is_read=False,
            **owner_filter(request),
        ).count()
        return Response({'unread_count': count})


class NotificationMarkReadView(APIView):
    """Mark a single notification as read."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification,
            pk=notification_id,
            **owner_filter(request),
        )
        notification.mark_read()
        return Response(NotificationSerializer(notification).data)


class NotificationMarkAllReadView(APIView):
    """Mark every unread notification as read."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request):
        updated = Notification.objects.filter(
            is_read=False,
            **owner_filter(request),
        ).update(is_read=True, read_at=timezone.now())
        return Response({'marked_read': updated})


class DailyMcqSubscriptionView(APIView):
    """Read or update the caller's "Subscribe for daily MCQ" preferences."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request):
        subscription = DailyMcqSubscription.objects.filter(
            **owner_filter(request),
        ).first()

        if subscription is None:
            # Report an unsubscribed default without persisting a row.
            automation = NotificationAutomation.objects.filter(
                kind=NotificationKind.DAILY_MCQ,
            ).first()
            return Response({
                'is_active': False,
                'questions_per_day': (
                    automation.default_questions_per_day if automation else 3
                ),
                'max_questions_per_day': DailyMcqSubscription.MAX_QUESTIONS_PER_DAY,
                'created_at': None,
                'updated_at': None,
            })

        return Response(DailyMcqSubscriptionSerializer(subscription).data)

    def put(self, request):
        return self._upsert(request)

    def post(self, request):
        return self._upsert(request)

    def _upsert(self, request):
        owner = owner_filter(request)
        subscription = DailyMcqSubscription.objects.filter(**owner).first()

        serializer = DailyMcqSubscriptionSerializer(
            subscription,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        if subscription is None:
            subscription = serializer.save(**owner)
        else:
            subscription = serializer.save()

        return Response(DailyMcqSubscriptionSerializer(subscription).data)


class DailyMcqDeliveryDetailView(APIView):
    """Fetch a delivered MCQ so the user can answer it from the notification."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request, delivery_id):
        delivery = get_object_or_404(
            DailyMcqDelivery.objects.select_related('question').prefetch_related(
                'question__answers',
            ),
            pk=delivery_id,
            **owner_filter(request),
        )
        return Response(DailyMcqDeliverySerializer(delivery).data)


class DailyMcqAnswerView(APIView):
    """Submit an answer, then reveal the correct option and explanation."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def post(self, request, delivery_id):
        delivery = get_object_or_404(
            DailyMcqDelivery.objects.select_related('question'),
            pk=delivery_id,
            **owner_filter(request),
        )

        if delivery.is_answered:
            # Answering is one-shot so the leaderboard cannot be farmed.
            return Response(
                {
                    'detail': 'This question was already answered.',
                    **DailyMcqDeliverySerializer(delivery).data,
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = SubmitMcqAnswerSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        answer = delivery.question.answers.filter(
            pk=serializer.validated_data['answer_id'],
        ).first()
        if answer is None:
            return Response(
                {'answer_id': 'That option does not belong to this question.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        delivery.record_answer(answer)
        return Response(DailyMcqDeliverySerializer(delivery).data)


class DailyMcqStatsView(APIView):
    """Personal correct/incorrect performance plus the monthly leaderboard."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]

    def get(self, request):
        is_guest = getattr(request.user, 'is_guest', False)
        stats = personal_stats(owner_filter(request))
        board = monthly_leaderboard(
            current_user_id=None if is_guest else request.user.id,
        )

        return Response({
            'performance': stats,
            'leaderboard_month': board['month'],
            'participant_count': board['participant_count'],
            'leaderboard': board['leaderboard'],
            'current_user_entry': board['current_user_entry'],
            # Guests are not ranked; surfaced so the app can explain why.
            'is_ranked': not is_guest,
        })


class DailyMcqHistoryView(ListAPIView):
    """Paginated history of delivered MCQs with their answer state."""

    authentication_classes = [FlexibleAuthentication]
    permission_classes = [IsAuthenticatedOrGuest]
    serializer_class = DailyMcqDeliverySerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return DailyMcqDelivery.objects.filter(
            **owner_filter(self.request),
        ).select_related('question').prefetch_related('question__answers')
