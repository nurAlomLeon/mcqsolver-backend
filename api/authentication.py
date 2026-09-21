# authentication.py - Fixed Guest User Authentication

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from django.contrib.auth.models import AnonymousUser
from .models import GuestUser
import uuid

class GuestAuthenticatedUser:
    """
    Custom user class for guest users that can be marked as authenticated
    """
    def __init__(self, guest_user):
        self.guest_user = guest_user
        self.is_guest = True
        self.is_authenticated = True
        self.is_anonymous = False
        self.is_active = True
        self.is_staff = False
        self.is_superuser = False
        self.id = None
        self.pk = None
        
    def __str__(self):
        return f"Guest({self.guest_user.guest_id})"
    
    def get_username(self):
        return f"guest_{self.guest_user.device_id}"
    
    @property
    def username(self):
        return self.get_username()

class GuestUserAuthentication(BaseAuthentication):
    """
    Custom authentication for guest users
    """
    def authenticate(self, request):
        # Check for guest authentication header
        guest_token = request.META.get('HTTP_X_GUEST_TOKEN')
        device_id = request.META.get('HTTP_X_DEVICE_ID')
        
        if guest_token and device_id:
            try:
                # Validate guest token format
                guest_uuid = uuid.UUID(guest_token)
                
                # Get or create guest user
                guest_user, created = GuestUser.objects.get_or_create(
                    device_id=device_id,
                    defaults={'guest_id': guest_uuid}
                )
                
                if not created:
                    if guest_user.guest_id != guest_uuid:
                        raise AuthenticationFailed('Invalid guest credentials.')
                    guest_user.save()  # This updates last_active due to auto_now=True
                
                # Create a custom authenticated user object for guests
                user = GuestAuthenticatedUser(guest_user)
                
                return (user, guest_token)
                
            except (ValueError, GuestUser.DoesNotExist):
                return None
        
        return None

class FlexibleAuthentication(BaseAuthentication):
    """
    Authentication that supports both regular users and guests
    """
    def authenticate(self, request):
        # First try token authentication for regular users
        from rest_framework.authentication import TokenAuthentication
        token_auth = TokenAuthentication()
        
        try:
            result = token_auth.authenticate(request)
            if result:
                user, token = result
                user.is_guest = False
                return result
        except:
            pass
        
        # Then try guest authentication
        guest_auth = GuestUserAuthentication()
        result = guest_auth.authenticate(request)
        if result:
            return result
        
        return None
