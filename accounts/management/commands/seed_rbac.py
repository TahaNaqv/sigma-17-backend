"""Seed default permissions and roles per WEBAPP_REQUIREMENTS permission matrix."""

from django.core.management.base import BaseCommand

from accounts.models import Permission, Role

PERMISSIONS = [
    # Users
    ("users.view", "View Users", "Users", "View user list and details"),
    ("users.create", "Create Users", "Users", "Create new users"),
    ("users.edit", "Edit Users", "Users", "Edit existing users"),
    ("users.delete", "Delete Users", "Users", "Delete users from the system"),
    # Roles
    ("roles.view", "View Roles", "Roles", "View role list and details"),
    ("roles.create", "Create Roles", "Roles", "Create new roles"),
    ("roles.edit", "Edit Roles", "Roles", "Edit existing roles"),
    ("roles.delete", "Delete Roles", "Roles", "Delete roles from the system"),
    # Permissions (Super Admin only)
    ("permissions.manage", "Manage Permissions", "Permissions", "Create and manage permissions"),
    # Files
    ("files.upload", "Upload Files", "Files", "Upload input files"),
    ("files.view", "View File List", "Files", "View list of input and output files"),
    # Processing
    ("module1.run", "Run Module 1", "Processing", "Run Module 1 processing"),
    ("module2.run", "Run Module 2", "Processing", "Run Module 2 processing"),
    # Outputs
    ("outputs.download", "Download Outputs", "Outputs", "Download generated output files"),
    # Dashboard and Run History
    ("dashboard.view", "View Dashboard", "Dashboard", "Access the main dashboard"),
    ("runhistory.view", "View Run History", "Run History", "View processing run history"),
]

ROLE_PERMISSIONS = {
    "Super Admin": [p[0] for p in PERMISSIONS],
    "Admin": [
        "users.view", "users.create", "users.edit", "users.delete",
        "roles.view", "roles.create", "roles.edit", "roles.delete",
        "files.upload", "files.view", "module1.run", "module2.run",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Actuary": [
        "files.upload", "files.view", "module1.run", "module2.run",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Analyst": [
        "files.upload", "files.view", "module1.run", "module2.run",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Viewer": [
        "files.view", "outputs.download", "dashboard.view", "runhistory.view",
    ],
}

ROLE_DESCRIPTIONS = {
    "Super Admin": "Full access to all system features and settings",
    "Admin": "Can manage users and roles; full access to processing",
    "Actuary": "Can upload files and run Module 1 & 2; view outputs",
    "Analyst": "Can upload files and run Module 1 & 2; view outputs",
    "Viewer": "Read-only access to dashboard, files, and outputs",
}


class Command(BaseCommand):
    help = "Seed default permissions and roles per WEBAPP_REQUIREMENTS"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Recreate roles and permissions (idempotent for permissions)",
        )

    def handle(self, *args, **options):
        force = options["force"]

        # Create permissions (idempotent)
        key_to_perm = {}
        for key, name, module, desc in PERMISSIONS:
            perm, created = Permission.objects.get_or_create(
                key=key,
                defaults={"name": name, "module": module, "description": desc},
            )
            if created:
                self.stdout.write(f"Created permission: {key}")
            key_to_perm[key] = perm

        # Create/update roles
        for role_name, perm_keys in ROLE_PERMISSIONS.items():
            role, created = Role.objects.get_or_create(
                name=role_name,
                defaults={"description": ROLE_DESCRIPTIONS.get(role_name, "")},
            )
            if force or created:
                role.description = ROLE_DESCRIPTIONS.get(role_name, "")
                role.save()
                perms = [key_to_perm[k] for k in perm_keys if k in key_to_perm]
                role.permissions.set(perms)
                self.stdout.write(
                    self.style.SUCCESS(f"Role '{role_name}' has {len(perms)} permissions")
                )
            elif created:
                self.stdout.write(f"Created role: {role_name}")

        self.stdout.write(self.style.SUCCESS("RBAC seed complete."))
