"""API tests for UW preview endpoint (requires DB)."""

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from module1_engine.tests.test_uw_patch import _minimal_combined_summary_bytes


class UwPreviewApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="uwtest",
            email="uwtest@example.com",
            password="testpass123",
        )
        role = Role.objects.create(name="Super Admin")
        self.user.profile.roles.add(role)
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
        role = Role.objects.create(name="Run History Only")
        perm, _ = Permission.objects.get_or_create(
            key="runhistory.view",
            defaults={
                "name": "View run history",
                "module": "Processing",
                "description": "",
            },
        )
        role.permissions.add(perm)
        user.profile.roles.add(role)
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
        role = Role.objects.create(name="Run History Only B")
        perm, _ = Permission.objects.get_or_create(
            key="runhistory.view",
            defaults={
                "name": "View run history",
                "module": "Processing",
                "description": "",
            },
        )
        role.permissions.add(perm)
        user.profile.roles.add(role)
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
