"""Static safety checks for the stock Keycloak local realm import."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REALM_PATH = ROOT / "keycloak" / "kaagaz-local-realm.json"
REALM = json.loads(REALM_PATH.read_text(encoding="utf-8"))


class KeycloakLocalRealmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = next(client for client in REALM["clients"] if client["clientId"] == "kaagaz-web")
        self.flows = {flow["alias"]: flow for flow in REALM["authenticationFlows"]}

    def test_environment_templates_have_safe_values_and_runtime_names(self) -> None:
        api_template = ROOT / "keycloak" / "api.env.example"
        admin_template = ROOT / "keycloak" / "admin.env.example"

        def values(path: Path) -> dict[str, str]:
            return {
                key: value
                for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#")
                for key, value in [line.split("=", maxsplit=1)]
            }

        self.assertEqual(
            {
                "KEYCLOAK_CLIENT_SECRET": "",
                "KEYCLOAK_ADMIN_CLIENT_ID": "kaagaz-provisioner",
                "KEYCLOAK_ADMIN_CLIENT_SECRET": "",
                "KEYCLOAK_FLOW_ENCRYPTION_KEY": "",
                "KEYCLOAK_ENABLED": "false",
            },
            values(api_template),
        )
        self.assertEqual(
            {
                "KC_BOOTSTRAP_ADMIN_USERNAME": "local-admin",
                "KC_BOOTSTRAP_ADMIN_PASSWORD": "",
                "KC_DB_PASSWORD": "",
            },
            values(admin_template),
        )

    def test_provisioner_is_a_server_only_client_with_only_user_management(self) -> None:
        client = next(c for c in REALM["clients"] if c["clientId"] == "kaagaz-provisioner")
        self.assertFalse(client["publicClient"])
        self.assertTrue(client["serviceAccountsEnabled"])
        self.assertFalse(client["standardFlowEnabled"])
        self.assertFalse(client["implicitFlowEnabled"])
        self.assertFalse(client["directAccessGrantsEnabled"])
        self.assertEqual([], client["redirectUris"])
        self.assertNotIn("secret", client)
        [account] = REALM["users"]  # the only user: no people are imported
        self.assertEqual("kaagaz-provisioner", account["serviceAccountClientId"])
        self.assertEqual({"realm-management": ["manage-users", "view-users"]}, account["clientRoles"])
        self.assertNotIn("realmRoles", account)
        self.assertNotIn("credentials", account)

    def test_realm_has_no_people_or_unconfigured_brokers(self) -> None:
        self.assertEqual(
            ["service-account-kaagaz-provisioner"], [user["username"] for user in REALM["users"]]
        )
        self.assertEqual([], REALM["identityProviders"])
        self.assertEqual([], REALM["identityProviderMappers"])
        self.assertFalse(REALM["registrationAllowed"])
        self.assertTrue(REALM["bruteForceProtected"])
        self.assertEqual("length(10) and notUsername", REALM["passwordPolicy"])  # owner: 10+, no symbol rules
        self.assertEqual("external", REALM["sslRequired"])
        self.assertEqual(("Kaagaz", "kaagaz", "kaagaz"), (REALM["displayName"], REALM["loginTheme"], REALM["emailTheme"]))
        self.assertTrue(REALM["resetPasswordAllowed"])
        self.assertEqual("totp", REALM["otpPolicyType"])
        self.assertEqual(6, REALM["otpPolicyDigits"])

    def test_client_is_confidential_code_flow_with_exact_local_redirect(self) -> None:
        self.assertFalse(self.client["publicClient"])
        self.assertEqual("client-secret", self.client["clientAuthenticatorType"])
        self.assertTrue(self.client["standardFlowEnabled"])
        self.assertFalse(self.client["implicitFlowEnabled"])
        self.assertFalse(self.client["directAccessGrantsEnabled"])
        self.assertFalse(self.client["serviceAccountsEnabled"])
        self.assertFalse(self.client["authorizationServicesEnabled"])
        self.assertEqual(
            [
                "http://localhost:3000/api/auth/keycloak/callback",
                "http://localhost:3000/login",  # where a setup email's "back to application" lands (#737)
            ],
            self.client["redirectUris"],
        )
        self.assertNotIn("secret", self.client)
        self.assertEqual("S256", self.client["attributes"]["pkce.code.challenge.method"])
        self.assertEqual("true", self.client["attributes"]["backchannel.logout.session.required"])
        self.assertEqual(
            "http://api:8000/api/auth/keycloak/backchannel-logout",
            self.client["attributes"]["backchannel.logout.url"],
        )
        self.assertIn("email", self.client["defaultClientScopes"])
        self.assertIn("profile", self.client["defaultClientScopes"])

    def test_mfa_is_required_on_local_and_broker_paths(self) -> None:
        local = self.flows["kaagaz-local-forms"]["authenticationExecutions"]
        broker = self.flows["kaagaz-broker-otp"]["authenticationExecutions"]
        self.assertIn(
            ("auth-username-password-form", "REQUIRED"),
            [(step["authenticator"], step["requirement"]) for step in local],
        )
        self.assertIn(
            ("auth-otp-form", "REQUIRED"),
            [(step["authenticator"], step["requirement"]) for step in local],
        )
        self.assertEqual("auth-otp-form", broker[0]["authenticator"])
        self.assertEqual("REQUIRED", broker[0]["requirement"])
        for flow in REALM["authenticationFlows"]:
            for step in flow["authenticationExecutions"]:
                self.assertNotEqual("auth-cookie", step.get("authenticator"))
                self.assertNotEqual("conditional-user-configured", step.get("authenticator"))

    def test_forgot_password_keeps_the_authenticator_and_never_deletes_it(self) -> None:
        """The built-in reset flow's reset-otp step deletes the user's OTP: a mailbox alone
        would then be enough to take an account over (#737)."""
        self.assertEqual("kaagaz-reset", REALM["resetCredentialsFlow"])
        steps = self.flows["kaagaz-reset"]["authenticationExecutions"]
        self.assertEqual(
            [
                ("reset-credentials-choose-user", "REQUIRED"),
                ("reset-credential-email", "REQUIRED"),
                ("auth-otp-form", "REQUIRED"),
                ("reset-password", "REQUIRED"),
            ],
            [(step["authenticator"], step["requirement"]) for step in steps],
        )
        for flow in REALM["authenticationFlows"]:
            for step in flow["authenticationExecutions"]:
                self.assertNotEqual("reset-otp", step.get("authenticator"))

    def test_amr_comes_from_executed_authenticators_with_finite_max_age(self) -> None:
        mappers = {mapper["protocolMapper"]: mapper for mapper in self.client["protocolMappers"]}
        self.assertIn("oidc-amr-mapper", mappers)
        self.assertIn("oidc-usersessionmodel-note-mapper", mappers)
        auth_time = next(mapper for mapper in self.client["protocolMappers"] if mapper["name"] == "Authentication time")["config"]
        self.assertEqual("AUTH_TIME", auth_time["user.session.note"])
        self.assertEqual("auth_time", auth_time["claim.name"])
        self.assertEqual("long", auth_time["jsonType.label"])
        self.assertEqual("true", auth_time["id.token.claim"])
        self.assertEqual("false", auth_time["access.token.claim"])
        self.assertEqual("false", auth_time["userinfo.token.claim"])
        note = mappers["oidc-usersessionmodel-note-mapper"]["config"]
        self.assertEqual("identity_provider", note["user.session.note"])
        self.assertEqual("authentication_provider", note["claim.name"])
        self.assertNotIn("hardcoded-claim", mappers)

        configs = {entry["alias"]: entry["config"] for entry in REALM["authenticatorConfig"]}
        self.assertEqual("pwd", configs["local password AMR reference"]["default.reference.value"])
        for alias in ("local password AMR reference", "local OTP AMR reference", "broker OTP AMR reference"):
            self.assertGreater(int(configs[alias]["default.reference.maxAge"]), 0)
        for alias in ("local OTP AMR reference", "broker OTP AMR reference"):
            self.assertEqual("otp", configs[alias]["default.reference.value"])


if __name__ == "__main__":
    unittest.main()
