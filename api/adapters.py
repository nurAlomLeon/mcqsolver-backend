from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from allauth.account.utils import user_email
from django.contrib.auth import get_user_model
from allauth.socialaccount.models import SocialAccount
from allauth.exceptions import ImmediateHttpResponse
from django.http import HttpResponseRedirect
from django.contrib.auth.models import User
import uuid

User = get_user_model()

class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    def pre_social_login(self, request, sociallogin):
        """
        Invoked just after a user successfully authenticates via a
        social provider, but before the login is actually processed.
        """
        # Get the email from the social account
        email = user_email(sociallogin.user)
        
        if email:
            try:
                # Try to find an existing user with this email
                existing_user = User.objects.get(email__iexact=email)
                
                # Check if this social account is already connected
                if not SocialAccount.objects.filter(
                    user=existing_user,
                    provider=sociallogin.account.provider,
                    uid=sociallogin.account.uid
                ).exists():
                    # Connect the social account to the existing user
                    sociallogin.connect(request, existing_user)
                    
            except User.DoesNotExist:
                # User doesn't exist, let the normal flow create them
                pass
            except User.MultipleObjectsReturned:
                # Multiple users with same email - use the first one
                existing_user = User.objects.filter(email__iexact=email).first()
                if not SocialAccount.objects.filter(
                    user=existing_user,
                    provider=sociallogin.account.provider,
                    uid=sociallogin.account.uid
                ).exists():
                    sociallogin.connect(request, existing_user)

    def save_user(self, request, sociallogin, form=None):
        """
        Saves a newly signed up social login. In case of auto-signup,
        the signup form is not available.
        """
        user = super().save_user(request, sociallogin, form)
        
        # CRITICAL FIX: Generate a unique username if it's empty
        if not user.username or user.username == '':
            # Generate a unique username based on email or Google ID
            email = user_email(sociallogin.user)
            if email:
                # Use email prefix as base username
                base_username = email.split('@')[0]
            else:
                # Fallback to google user id
                base_username = f"google_user_{sociallogin.account.uid}"
            
            # Ensure username is unique
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base_username}_{counter}"
                counter += 1
            
            user.username = username
            print(f"Generated unique username: {username} for email: {email}")
        
        # Extract additional info from Google profile
        if sociallogin.account.provider == 'google':
            extra_data = sociallogin.account.extra_data
            if extra_data:
                # Set first and last name from Google profile
                if not user.first_name and 'given_name' in extra_data:
                    user.first_name = extra_data['given_name']
                if not user.last_name and 'family_name' in extra_data:
                    user.last_name = extra_data['family_name']
                
                # If no first/last name, try to split the full name
                if not user.first_name and not user.last_name and 'name' in extra_data:
                    name_parts = extra_data['name'].split()
                    if len(name_parts) >= 1:
                        user.first_name = name_parts[0]
                    if len(name_parts) >= 2:
                        user.last_name = ' '.join(name_parts[1:])
                
                user.save()
        
        return user
    
    def is_auto_signup_allowed(self, request, sociallogin):
        """
        Allow auto-signup for all Google users
        """
        # Always allow auto-signup
        return True