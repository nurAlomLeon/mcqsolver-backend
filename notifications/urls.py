from django.urls import path

from .views import (
    DailyMcqAnswerView,
    DailyMcqDeliveryDetailView,
    DailyMcqHistoryView,
    DailyMcqStatsView,
    DailyMcqSubscriptionView,
    DeviceInstallationView,
    NotificationListView,
    NotificationMarkAllReadView,
    NotificationMarkReadView,
    NotificationUnreadCountView,
)

urlpatterns = [
    path('installations/', DeviceInstallationView.as_view(), name='device-installation'),
    path('', NotificationListView.as_view(), name='notification-list'),
    path('unread-count/', NotificationUnreadCountView.as_view(), name='notification-unread-count'),
    path('read-all/', NotificationMarkAllReadView.as_view(), name='notification-read-all'),

    # Daily MCQ
    path('daily-mcq/', DailyMcqSubscriptionView.as_view(), name='daily-mcq-subscription'),
    path('daily-mcq/stats/', DailyMcqStatsView.as_view(), name='daily-mcq-stats'),
    path('daily-mcq/history/', DailyMcqHistoryView.as_view(), name='daily-mcq-history'),
    path(
        'daily-mcq/deliveries/<int:delivery_id>/',
        DailyMcqDeliveryDetailView.as_view(),
        name='daily-mcq-delivery',
    ),
    path(
        'daily-mcq/deliveries/<int:delivery_id>/answer/',
        DailyMcqAnswerView.as_view(),
        name='daily-mcq-answer',
    ),

    path('<int:notification_id>/read/', NotificationMarkReadView.as_view(), name='notification-read'),
]
