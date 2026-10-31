from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        email = (kwargs.get("email") or username or "").strip().lower()
        if not email or password is None:
            return None
        user = User.objects.filter(email=email).first()
        if user is None:
            User().set_password(password)  # equalise timing
        elif user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
