"""Seed default permissions and roles.

In the multi-tenant model:
- Permission and Role records are GLOBAL (one catalog shared by all tenants).
- Role assignment is per-organization via tenants.Membership.
- Global super-admin = user.is_superuser (NOT a per-membership role).

The "Super Admin" Role record below is kept for catalog completeness and so
it can show up in role pickers, but the effective super-admin bypass is
controlled by user.is_superuser.
"""

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
    # Organizations (Super Admin only)
    ("orgs.view", "View Organizations", "Organizations", "View list of organizations"),
    ("orgs.create", "Create Organizations", "Organizations", "Create new organizations"),
    ("orgs.edit", "Edit Organizations", "Organizations", "Edit organization details"),
    ("orgs.delete", "Delete Organizations", "Organizations", "Delete organizations"),
    # Memberships (org admins)
    ("memberships.view", "View Memberships", "Memberships", "View members of an organization"),
    ("memberships.manage", "Manage Memberships", "Memberships", "Add, update, or remove organization members"),
    # Files
    ("files.upload", "Upload Files", "Files", "Upload input files"),
    ("files.view", "View File List", "Files", "View list of input and output files"),
    # Datasets (UI-driven data entry — replaces or supplements Excel upload)
    ("datasets.view", "View Datasets", "Datasets", "View datasets and rows"),
    ("datasets.edit", "Edit Datasets", "Datasets", "Create, edit, and add rows to datasets"),
    ("datasets.lock", "Lock Datasets", "Datasets", "Lock a dataset so its rows become read-only"),
    ("datasets.delete", "Delete Datasets", "Datasets", "Delete datasets and their rows"),
    # Processing
    ("upr_policy.view", "View UPR Policy", "Processing", "View UPR earning-method policies"),
    ("upr_policy.manage", "Manage UPR Policy", "Processing", "Create and activate UPR earning-method policies"),
    ("class_alias.view", "View Class Aliases", "Processing", "View reserving-class alias mappings"),
    ("class_alias.manage", "Manage Class Aliases", "Processing", "Map a claims-file reserving class onto the premium-file spelling"),
    ("scenarios.view", "View Scenario Sets", "Processing", "View sensitivity scenario sets"),
    ("scenarios.manage", "Manage Scenario Sets", "Processing", "Create and edit sensitivity scenario sets"),
    ("module1.run", "Run Module 1", "Processing", "Run Module 1 processing"),
    ("module2.run", "Run Module 2", "Processing", "Run Module 2 processing"),
    # Outputs
    ("outputs.download", "Download Outputs", "Outputs", "Download generated output files"),
    # Dashboard and Run History
    ("dashboard.view", "View Dashboard", "Dashboard", "Access the main dashboard"),
    ("runhistory.view", "View Run History", "Run History", "View processing run history"),
]

# Note: "Super Admin" Role record exists for catalog completeness only.
# Effective super-admin bypass is gated by user.is_superuser, so the Role's
# permission set here is not consulted by the permission checks at runtime.
ROLE_PERMISSIONS = {
    "Super Admin": [p[0] for p in PERMISSIONS],
    "Admin": [
        # Org admin: manage users + memberships inside the org, view roles
        "users.view", "users.create", "users.edit", "users.delete",
        "roles.view",
        "memberships.view", "memberships.manage",
        "files.upload", "files.view", "module1.run", "module2.run",
        "scenarios.view", "scenarios.manage",
        "upr_policy.view", "upr_policy.manage",
        "class_alias.view", "class_alias.manage",
        "datasets.view", "datasets.edit", "datasets.lock", "datasets.delete",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Actuary": [
        "files.upload", "files.view", "module1.run", "module2.run",
        "scenarios.view", "scenarios.manage",
        "upr_policy.view", "upr_policy.manage",
        "class_alias.view", "class_alias.manage",
        "datasets.view", "datasets.edit", "datasets.lock", "datasets.delete",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Analyst": [
        "files.upload", "files.view", "module1.run", "module2.run",
        "scenarios.view", "upr_policy.view", "class_alias.view",
        "datasets.view", "datasets.edit",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
    "Viewer": [
        "files.view", "datasets.view", "scenarios.view", "upr_policy.view",
        "outputs.download", "dashboard.view", "runhistory.view",
    ],
}

ROLE_DESCRIPTIONS = {
    "Super Admin": "Global super admin (effective access controlled by user.is_superuser flag).",
    "Admin": "Organization admin: manage users and memberships within the organization.",
    "Actuary": "Can upload files and run Module 1 & 2; view outputs.",
    "Analyst": "Can upload files and run Module 1 & 2; view outputs.",
    "Viewer": "Read-only access to dashboard, files, and outputs.",
}


class Command(BaseCommand):
    help = "Seed default permissions and roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Reset role permission sets to the seeded defaults.",
        )

    def handle(self, *args, **options):
        force = options["force"]

        key_to_perm = {}
        for key, name, module, desc in PERMISSIONS:
            perm, created = Permission.objects.get_or_create(
                key=key,
                defaults={"name": name, "module": module, "description": desc},
            )
            if created:
                self.stdout.write(f"Created permission: {key}")
            key_to_perm[key] = perm

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
