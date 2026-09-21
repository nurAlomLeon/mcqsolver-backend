from django import forms

from .models import NotificationCampaign


class NotificationCampaignForm(forms.ModelForm):
    class Meta:
        model = NotificationCampaign
        fields = ['title', 'message', 'audience', 'course', 'status', 'scheduled_at']


class SendNotificationConfirmationForm(forms.Form):
    """Explicit, no-default-value confirmation step before a broadcast.

    Rendered as an intermediate admin page so a stray click on the action
    dropdown can never trigger a real push notification.
    """

    confirm = forms.BooleanField(
        label='I understand this will send a push notification to the selected '
        'audience immediately.',
        required=True,
    )
