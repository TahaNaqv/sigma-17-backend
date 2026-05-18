"""Bootstrap the default organization and a global Super Admin user.

Idempotent. Reads from environment variables:
  SIGMA17_DEFAULT_ORG_NAME    (default: "Sigma 17")
  SIGMA17_SUPERUSER_EMAIL     (default: "admin@sigma17.local")
  SIGMA17_SUPERUSER_PASSWORD  (default: "ChangeMe123!")
  SIGMA17_SUPERUSER_NAME      (default: "Super Admin")
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Role
from tenants.models import Membership, Organization

User = get_user_model()


class Command(BaseCommand):
    help = "Create the default organization and superuser. Idempotent."

    @transaction.atomic
    def handle(self, *args, **options):
        org_name = os.environ.get("SIGMA17_DEFAULT_ORG_NAME", "Sigma 17")
        email = os.environ.get("SIGMA17_SUPERUSER_EMAIL", "admin@sigma17.local")
        password = os.environ.get("SIGMA17_SUPERUSER_PASSWORD", "ChangeMe123!")
        name = os.environ.get("SIGMA17_SUPERUSER_NAME", "Super Admin")
        parts = name.strip().split(None, 1)
        first_name = parts[0] if parts else ""
        last_name = parts[1] if len(parts) > 1 else ""

        org, org_created = Organization.objects.get_or_create(
            name=org_name,
            defaults={"description": "Default organization"},
        )
        if org_created:
            self.stdout.write(self.style.SUCCESS(f"Created organization '{org_name}'"))
        else:
            self.stdout.write(f"Organization '{org_name}' already exists")

        user, user_created = User.objects.get_or_create(
            email=email,
            defaults={
                "username": email,
                "first_name": first_name,
                "last_name": last_name,
                "is_active": True,
                "is_staff": True,
                "is_superuser": True,
            },
        )
        if user_created:
            user.set_password(password)
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f"Created superuser {email} (password: {password})")
            )
        else:
            # Ensure flags are correct even if the user already exists
            changed = False
            if not user.is_superuser:
                user.is_superuser = True
                changed = True
            if not user.is_staff:
                user.is_staff = True
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            if changed:
                user.save()
            self.stdout.write(f"Superuser {email} already exists")

        if not org.created_by_id:
            org.created_by = user
            org.save(update_fields=["created_by"])

        profile = user.profile
        if not profile.active_organization_id:
            profile.active_organization = org
            profile.save(update_fields=["active_organization"])

        membership, m_created = Membership.objects.get_or_create(
            user=user, organization=org, defaults={"status": "active"}
        )
        # Assign Admin role inside the default org if available, so the
        # superuser shows up with the expected role badges in the UI.
        admin_role = Role.objects.filter(name="Admin").first()
        if admin_role and not membership.roles.filter(pk=admin_role.pk).exists():
            membership.roles.add(admin_role)

        if m_created:
            self.stdout.write(
                self.style.SUCCESS(f"Added {email} to '{org_name}' as a member")
            )

        self.stdout.write(self.style.SUCCESS("Bootstrap complete."))
