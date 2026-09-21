from rest_framework.permissions import BasePermission

from admissionlife.models import Enrollment


class IsAuthenticatedUser(BasePermission):
    """
    Allow access to authenticated users (regular or guest).
    Both Google-authenticated users and guest users (via X-Guest-Token header)
    are permitted.
    """

    message = "Authentication is required."

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
        )


class IsEnrolledInBatch(BasePermission):
    """
    Allow access only to users who are enrolled in the batch
    identified by 'batch_id' or 'pk' in the URL kwargs.
    """

    message = "You are not enrolled in this batch."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        # Look for batch_id in URL kwargs; try batch_id, batch_pk, then pk
        batch_id = (
            view.kwargs.get("batch_id")
            or view.kwargs.get("batch_pk")
            or view.kwargs.get("pk")
        )
        if batch_id is None:
            return False

        return Enrollment.objects.filter(
            user=request.user, batch_id=batch_id
        ).exists()
