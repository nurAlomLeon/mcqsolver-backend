def custom_user_display(user):
    """
    Returns a string representation of the user, guaranteed not to return a translation proxy.
    This fixes the `__str__ returned non-string (type __proxy__)` error in Django Admin for SocialAccounts.
    """
    return user.email or user.username or "User"
