"""Custom JWT serializer to support login by username or email."""

from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        username_or_email = attrs.get("username", "").strip()
        password = attrs.get("password")

        if "@" in username_or_email:
            user = User.objects.filter(email__iexact=username_or_email).first()
        else:
            user = User.objects.filter(username__iexact=username_or_email).first()

        if user and user.check_password(password):
            # Parent expects attrs["username"] for token claims; use actual username
            attrs["username"] = user.username
            attrs["user"] = user
            return super().validate(attrs)

        # Fall through to parent validation (will raise AuthenticationFailed)
        return super().validate(attrs)
