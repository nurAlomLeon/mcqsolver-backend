# permissions.py - Updated for Fixed Guest Authentication

from rest_framework.permissions import BasePermission

class IsAuthenticatedOrGuest(BasePermission):
    """
    Allow access to authenticated users or guests
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            (request.user.is_authenticated or getattr(request.user, 'is_guest', False))
        )

class IsAuthenticatedUserOnly(BasePermission):
    """
    Allow access only to authenticated (non-guest) users
    """
    def has_permission(self, request, view):
        return bool(
            request.user and 
            request.user.is_authenticated and 
            not getattr(request.user, 'is_guest', False)
        )

class GuestLimitedPermission(BasePermission):
    """
    Limited permissions for guest users
    """
    def has_permission(self, request, view):
        if not request.user or not hasattr(request.user, 'is_authenticated'):
            return False
        
        if not request.user.is_authenticated:
            return False
        
        # If it's a guest user, check limitations
        if getattr(request.user, 'is_guest', False):
            # Guests can only perform safe operations
            if request.method in ['GET', 'HEAD', 'OPTIONS']:
                return True
            # Allow POST for specific actions (like taking quizzes, saving questions)
            allowed_actions = [
                'start_exam', 'submit_bulk_answers', 'submit_answer', 
                'create', 'bulk_save', 'set_targets'
            ]
            if request.method == 'POST' and hasattr(view, 'action') and view.action in allowed_actions:
                return True
            # Allow DELETE for removing saved questions
            if request.method == 'DELETE' and 'saved-question' in request.path:
                return True
            return False
        
        # Regular users have full access
        return True