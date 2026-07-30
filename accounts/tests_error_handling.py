"""API error-contract tests: responses are always JSON with a usable message."""
import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Permission, Role
from tenants.models import Membership, Organization

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class UserApiErrorContractTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Acme", slug="acme")
        self.admin = User.objects.create_user(
            username="admin@acme.test", email="admin@acme.test", password="pw-admin-123"
        )
        perm_create, _ = Permission.objects.get_or_create(
            key="users.create", defaults={"name": "Create users", "module": "access"}
        )
        perm_view, _ = Permission.objects.get_or_create(
            key="users.view", defaults={"name": "View users", "module": "access"}
        )
        self.role = Role.objects.create(name="Admin")
        self.role.permissions.set([perm_create, perm_view])
        membership = Membership.objects.create(
            user=self.admin, organization=self.org, status="active"
        )
        membership.roles.set([self.role])
        profile = self.admin.profile
        profile.active_organization = self.org
        profile.save()

        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.client.credentials(HTTP_X_ORGANIZATION_ID=str(self.org.id))

    def _post_user(self, **overrides):
        payload = {
            "name": "New User",
            "email": "new.user@acme.test",
            "status": "active",
            "roleIds": [],
        }
        payload.update(overrides)
        return self.client.post("/api/users/", payload, format="json")

    def test_create_user_without_password_succeeds(self):
        """The original bug: blank password 500'd on make_random_password()."""
        res = self._post_user()
        self.assertEqual(res.status_code, 201, res.content)
        user = User.objects.get(email="new.user@acme.test")
        self.assertTrue(user.has_usable_password())

    def test_duplicate_email_returns_field_error_not_500(self):
        first = self._post_user()
        self.assertEqual(first.status_code, 201, first.content)

        second = self._post_user()
        self.assertEqual(second.status_code, 400, second.content)
        body = second.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("email", body["fieldErrors"])
        self.assertIn("already exists", body["fieldErrors"]["email"][0])
        # `detail` must be a plain, displayable string.
        self.assertIsInstance(body["detail"], str)
        self.assertIn("already exists", body["detail"])

    def test_invalid_email_yields_field_error(self):
        res = self._post_user(email="not-an-email")
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertIn("email", body["fieldErrors"])
        self.assertIsInstance(body["detail"], str)

    def test_short_password_yields_field_error(self):
        res = self._post_user(password="short")
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertIn("password", body["fieldErrors"])

    def test_error_bodies_are_json_never_html(self):
        res = self._post_user(email="not-an-email")
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")
        self.assertNotIn(b"<!doctype html", res.content.lower())
        json.loads(res.content)

    def test_unauthenticated_request_returns_json_401(self):
        anon = APIClient()
        res = anon.get("/api/users/")
        self.assertEqual(res.status_code, 401)
        body = res.json()
        self.assertIsInstance(body["detail"], str)
        self.assertNotIn("<", body["detail"])

    def test_permission_denied_returns_json_403(self):
        self.role.permissions.clear()
        res = self.client.get("/api/users/")
        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertEqual(body["code"], "permission_denied")
        self.assertIsInstance(body["detail"], str)

    def test_unknown_api_route_returns_json_404(self):
        res = self.client.get("/api/does-not-exist/")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")
        self.assertIsInstance(res.json()["detail"], str)

    def test_missing_required_fields_names_the_field(self):
        res = self.client.post("/api/users/", {}, format="json")
        self.assertEqual(res.status_code, 400, res.content)
        body = res.json()
        self.assertIn("name", body["fieldErrors"])
        self.assertIn("email", body["fieldErrors"])


@override_settings(SECURE_SSL_REDIRECT=False)
class UnhandledExceptionTests(TestCase):
    """An unexpected exception inside a DRF view must still return JSON 500."""

    def test_unhandled_exception_is_json_with_request_id(self):
        from unittest.mock import patch

        org = Organization.objects.create(name="Beta", slug="beta")
        user = User.objects.create_user(
            username="u@beta.test", email="u@beta.test", password="pw-12345678"
        )
        perm, _ = Permission.objects.get_or_create(
            key="users.view", defaults={"name": "View users", "module": "access"}
        )
        role = Role.objects.create(name="Viewer")
        role.permissions.set([perm])
        m = Membership.objects.create(user=user, organization=org, status="active")
        m.roles.set([role])
        profile = user.profile
        profile.active_organization = org
        profile.save()

        client = APIClient()
        client.force_authenticate(user=user)
        client.credentials(HTTP_X_ORGANIZATION_ID=str(org.id))

        with patch(
            "accounts.views.UserViewSet.get_queryset",
            side_effect=RuntimeError("boom"),
        ):
            res = client.get("/api/users/")

        self.assertEqual(res.status_code, 500)
        self.assertEqual(res["Content-Type"].split(";")[0], "application/json")
        body = res.json()
        self.assertEqual(body["code"], "server_error")
        self.assertNotIn("boom", body["detail"])  # internals not leaked
        self.assertIn("requestId", body)
