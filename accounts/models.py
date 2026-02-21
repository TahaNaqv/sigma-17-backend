from django.conf import settings
from django.db import models


class Permission(models.Model):
    """Granular permission with a unique key for RBAC checks."""
    key = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    module = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.key} ({self.name})"


class Role(models.Model):
    """Role with M2M to permissions."""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    permissions = models.ManyToManyField(Permission, related_name="roles", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class UserProfile(models.Model):
    """Links Django User to RBAC roles."""
    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    roles = models.ManyToManyField(Role, related_name="user_profiles", blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Profile for {self.user.email}"
