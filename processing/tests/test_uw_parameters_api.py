"""API tests for UW preview endpoint (requires DB)."""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from module1_engine.tests.test_uw_patch import _minimal_combined_summary_bytes
from tenants.models import Membership, Organization


def _give_role_with_permissions(
    user: User, role_name: str, perm_keys: list[str], org: Organization
) -> None:
    """Assign a permission role to `user` within `org`. Roles are scoped to an
    organization via tenants.Membership in the multi-tenant model."""
    user.profile.active_organization = org
    user.profile.save(update_fields=["active_organization"])
    role = Role.objects.create(name=role_name)
    for key in perm_keys:
        perm, _ = Permission.objects.get_or_create(
            key=key,
            defaults={"name": key, "module": "Processing", "description": ""},
        )
        role.permissions.add(perm)
    membership = Membership.objects.create(user=user, organization=org, status="active")
    membership.roles.add(role)


@override_settings(SECURE_SSL_REDIRECT=False)
class UwPreviewApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="uw-org")
        self.user = User.objects.create_user(
            username="uwtest",
            email="uwtest@example.com",
            password="testpass123",
        )
        _give_role_with_permissions(
            self.user, "Module1 Runner", ["module1.run", "runhistory.view"], self.org
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_preview_400_without_file(self):
        res = self.client.post("/api/module1/combined-summary/uw-preview/", {})
        self.assertEqual(res.status_code, 400)

    def test_preview_200_with_workbook(self):
        raw = _minimal_combined_summary_bytes()
        upload = SimpleUploadedFile(
            "Combined_Summary.xlsx",
            raw,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        res = self.client.post(
            "/api/module1/combined-summary/uw-preview/",
            {"combined_summary": upload},
            format="multipart",
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertIn("exp_ratio", body)
        self.assertIn("ulae_ra", body)
        self.assertIn("discount", body)

    def test_preview_403_without_module1_run(self):
        user = User.objects.create_user(
            username="nom1run",
            email="nom1@example.com",
            password="testpass123",
        )
        _give_role_with_permissions(
            user, "Run History Only", ["runhistory.view"], self.org
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.post("/api/module1/combined-summary/uw-preview/", {})
        self.assertEqual(res.status_code, 403)

    def test_uw_parameters_job_403_without_module1_run(self):
        user = User.objects.create_user(
            username="nom1run2",
            email="nom1b@example.com",
            password="testpass123",
        )
        _give_role_with_permissions(
            user, "Run History Only B", ["runhistory.view"], self.org
        )
        raw = _minimal_combined_summary_bytes()
        upload = SimpleUploadedFile(
            "Combined_Summary.xlsx",
            raw,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        client = APIClient()
        client.force_authenticate(user=user)
        res = client.post(
            "/api/module1/jobs/uw-parameters/",
            {
                "combined_summary": upload,
                "payload": '{"exp_ratio":[],"ulae_ra":[],"discount":[]}',
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, 403)
