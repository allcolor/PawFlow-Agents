"""Tests for AuthGateway, auth providers, rule evaluator, and rate limiter."""

import time
import unittest

from services.auth_providers.base import AuthResult, RateLimiter
from services.auth_providers.rule_evaluator import evaluate_rule


class TestRuleEvaluator(unittest.TestCase):
    """Test expression-based provisioning rules."""

    def test_email_endswith(self):
        claims = {"email": "admin@mycompany.com", "provider": "google"}
        self.assertTrue(evaluate_rule("email.endswith('@mycompany.com')", claims))
        self.assertFalse(evaluate_rule("email.endswith('@other.com')", claims))

    def test_exact_email(self):
        claims = {"email": "admin@gmail.com"}
        self.assertTrue(evaluate_rule("email == 'admin@gmail.com'", claims))
        self.assertFalse(evaluate_rule("email == 'other@gmail.com'", claims))

    def test_hosted_domain(self):
        claims = {"hd": "mycompany.com", "email": "user@mycompany.com"}
        self.assertTrue(evaluate_rule("hd == 'mycompany.com'", claims))

    def test_provider_check(self):
        claims = {"provider": "google", "email_verified": True}
        self.assertTrue(evaluate_rule("provider == 'google' and email_verified", claims))
        self.assertFalse(evaluate_rule("provider == 'github'", claims))

    def test_regex_match(self):
        claims = {"email": "user@partner1.org"}
        self.assertTrue(evaluate_rule(
            "re.match(r'.*@(partner1|partner2)\\.org$', email)", claims))
        claims2 = {"email": "user@random.org"}
        self.assertFalse(evaluate_rule(
            "re.match(r'.*@(partner1|partner2)\\.org$', email)", claims2))

    def test_or_condition(self):
        claims = {"email": "cto@gmail.com"}
        self.assertTrue(evaluate_rule(
            "email == 'admin@gmail.com' or email == 'cto@gmail.com'", claims))

    def test_empty_expression(self):
        self.assertFalse(evaluate_rule("", {"email": "test"}))
        self.assertFalse(evaluate_rule("   ", {"email": "test"}))

    def test_invalid_expression(self):
        self.assertFalse(evaluate_rule("definitely not python", {"email": "test"}))

    def test_blocked_dunder(self):
        self.assertFalse(evaluate_rule("__import__('os')", {"email": "test"}))

    def test_missing_variable(self):
        self.assertFalse(evaluate_rule("nonexistent_var == 'x'", {}))

    def test_boolean_true_false(self):
        claims = {"email_verified": True}
        self.assertTrue(evaluate_rule("email_verified == true", claims))
        self.assertTrue(evaluate_rule("email_verified == True", claims))

    def test_len_function(self):
        claims = {"groups": ["admin", "dev"]}
        self.assertTrue(evaluate_rule("len(groups) > 1", claims))

    def test_in_operator(self):
        claims = {"email": "user@mycompany.com", "hd": "mycompany.com"}
        self.assertTrue(evaluate_rule("'mycompany' in hd", claims))


class TestRateLimiter(unittest.TestCase):
    """Test rate limiter with LRU eviction."""

    def test_first_check_allowed(self):
        rl = RateLimiter()
        allowed, wait = rl.check("1.2.3.4")
        self.assertTrue(allowed)
        self.assertEqual(wait, 0)

    def test_failure_blocks(self):
        rl = RateLimiter(base_delay=2.0)
        rl.record_failure("1.2.3.4")
        allowed, wait = rl.check("1.2.3.4")
        self.assertFalse(allowed)
        self.assertGreaterEqual(wait, 1)

    def test_success_clears(self):
        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("1.2.3.4")
        rl.record_success("1.2.3.4")
        allowed, _ = rl.check("1.2.3.4")
        self.assertTrue(allowed)

    def test_exponential_backoff(self):
        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("1.2.3.4")
        _, wait1 = rl.check("1.2.3.4")
        # Wait it out
        time.sleep(1.1)
        rl.record_failure("1.2.3.4")
        _, wait2 = rl.check("1.2.3.4")
        self.assertGreater(wait2, wait1)  # doubled

    def test_max_entries_eviction(self):
        rl = RateLimiter(max_entries=3, base_delay=1.0)
        for i in range(5):
            rl.record_failure(f"ip_{i}")
        # Only 3 should remain
        self.assertEqual(len(rl._entries), 3)
        # Oldest (ip_0, ip_1) should be evicted
        allowed, _ = rl.check("ip_0")
        self.assertTrue(allowed)  # evicted = no record = allowed

    def test_ttl_expiry(self):
        rl = RateLimiter(ttl=1, base_delay=0.5)
        rl.record_failure("1.2.3.4")
        time.sleep(1.1)
        allowed, _ = rl.check("1.2.3.4")
        self.assertTrue(allowed)  # expired

    def test_different_ips_independent(self):
        rl = RateLimiter(base_delay=1.0)
        rl.record_failure("1.2.3.4")
        allowed, _ = rl.check("5.6.7.8")
        self.assertTrue(allowed)


class TestAuthResult(unittest.TestCase):
    """Test AuthResult dataclass."""

    def test_success_result(self):
        r = AuthResult(success=True, user_id="u1", username="bob",
                       email="bob@test.com", provider="google")
        self.assertTrue(r.success)
        self.assertEqual(r.provider, "google")

    def test_failure_result(self):
        r = AuthResult(success=False, error="Invalid credentials")
        self.assertFalse(r.success)
        self.assertEqual(r.error, "Invalid credentials")

    def test_claims_dict(self):
        r = AuthResult(success=True, claims={"email": "a@b.com", "hd": "b.com"})
        self.assertEqual(r.claims["hd"], "b.com")


class TestBuiltinProvider(unittest.TestCase):
    """Test builtin (username/password) provider."""

    def setUp(self):
        from core.security import SecurityManager, Role
        self.sm = SecurityManager.get_instance()
        # Create test user
        try:
            self.sm.create_user("testauth", "pass123", Role.USER,
                                email="test@example.com")
        except ValueError:
            pass  # already exists

    def test_valid_login(self):
        from services.auth_providers.builtin import BuiltinAuthProvider
        p = BuiltinAuthProvider()
        result = p.validate_credentials("testauth", "pass123")
        self.assertTrue(result.success)
        self.assertEqual(result.username, "testauth")
        self.assertEqual(result.provider, "builtin")
        self.assertIn("user", result.roles)

    def test_wrong_password(self):
        from services.auth_providers.builtin import BuiltinAuthProvider
        p = BuiltinAuthProvider()
        result = p.validate_credentials("testauth", "wrongpass")
        self.assertFalse(result.success)

    def test_unknown_user(self):
        from services.auth_providers.builtin import BuiltinAuthProvider
        p = BuiltinAuthProvider()
        result = p.validate_credentials("nonexistent", "pass")
        self.assertFalse(result.success)

    def test_not_oauth(self):
        from services.auth_providers.builtin import BuiltinAuthProvider
        p = BuiltinAuthProvider()
        self.assertFalse(p.is_oauth)


class TestAuthGatewayService(unittest.TestCase):
    """Test AuthGatewayService orchestration."""

    def test_provider_registration(self):
        from services.auth_gateway_service import _PROVIDER_CLASSES
        self.assertIn("builtin", _PROVIDER_CLASSES)
        self.assertIn("google", _PROVIDER_CLASSES)
        self.assertIn("github", _PROVIDER_CLASSES)
        self.assertIn("generic", _PROVIDER_CLASSES)
        self.assertGreaterEqual(len(_PROVIDER_CLASSES), 9)

    def test_enabled_providers(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({
            "providers": {
                "builtin": {"enabled": True},
                "google": {"enabled": True, "client_id": "x", "client_secret": "y"},
            }
        })
        gw._create_connection()
        providers = gw.get_enabled_providers()
        names = [p["name"] for p in providers]
        self.assertIn("builtin", names)
        self.assertIn("google", names)

    def test_disabled_provider(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({
            "providers": {
                "builtin": {"enabled": False},
                "google": {"enabled": True, "client_id": "x", "client_secret": "y"},
            }
        })
        gw._create_connection()
        names = [p["name"] for p in gw.get_enabled_providers()]
        self.assertNotIn("builtin", names)

    def test_generic_fallback(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({
            "providers": {
                "my_sso": {
                    "enabled": True,
                    "client_id": "x", "client_secret": "y",
                    "authorize_url": "https://sso.example.com/auth",
                    "token_url": "https://sso.example.com/token",
                    "userinfo_url": "https://sso.example.com/userinfo",
                }
            }
        })
        gw._create_connection()
        names = [p["name"] for p in gw.get_enabled_providers()]
        self.assertIn("my_sso", names)

    def test_state_generation(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({"providers": {}})
        state = gw.generate_state("google")
        self.assertTrue(len(state) > 20)
        # Validate
        data = gw.validate_state(state)
        self.assertIsNotNone(data)
        self.assertEqual(data["provider"], "google")
        # Second validate should fail (consumed)
        self.assertIsNone(gw.validate_state(state))

    def test_rate_limit_check(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({"providers": {}, "rate_limit_base_delay": 1})
        allowed, _ = gw.check_rate_limit("1.2.3.4")
        self.assertTrue(allowed)

    def test_oauth_failures_do_not_increment_builtin_login_rate_limit(self):
        from services.auth_gateway_service import AuthGatewayService
        from services.auth_providers.base import AuthResult

        class FailingOAuthProvider:
            def exchange_code(self, code, redirect_uri):
                return AuthResult(success=False, error="provider rejected credentials")

        gw = AuthGatewayService({"providers": {}, "rate_limit_base_delay": 1})
        gw._providers["github"] = FailingOAuthProvider()
        result = gw.authenticate_oauth("github", "bad-code", "https://webchat.example/auth/callback", ip="1.2.3.4")
        self.assertFalse(result.success)
        allowed, _ = gw.check_rate_limit("1.2.3.4")
        self.assertTrue(allowed)

    def test_github_token_exchange_credential_error_is_contextualized(self):
        from services.auth_gateway_service import AuthGatewayService
        from services.auth_providers.base import AuthResult

        class FailingGitHubProvider:
            def exchange_code(self, code, redirect_uri):
                return AuthResult(success=False, error="The client_id and/or client_secret passed are incorrect.")

        gw = AuthGatewayService({"providers": {}})
        gw._providers["github"] = FailingGitHubProvider()
        result = gw.authenticate_oauth("github", "code", "https://webchat.example/auth/callback")
        self.assertFalse(result.success)
        self.assertIn("browser authorization", result.error)
        self.assertIn("callback token exchange", result.error)
        self.assertNotEqual(result.error, "The client_id and/or client_secret passed are incorrect.")

    def test_builtin_auth(self):
        from core.security import SecurityManager, Role
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("gw_test", "pass", Role.USER)
        except ValueError:
            pass

        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({"providers": {"builtin": {"enabled": True}}})
        gw._create_connection()
        result = gw.authenticate_builtin("gw_test", "pass")
        self.assertTrue(result.success)

    def test_builtin_auth_wrong_pass(self):
        from services.auth_gateway_service import AuthGatewayService
        gw = AuthGatewayService({"providers": {"builtin": {"enabled": True}}})
        gw._create_connection()
        result = gw.authenticate_builtin("gw_test", "wrong")
        self.assertFalse(result.success)

    def test_admin_link_claim_maps_oauth_identity_to_existing_admin(self):
        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        from services.auth_gateway_service import AuthGatewayService

        IdentityService.reset()
        SecurityManager._instance = None
        sm = SecurityManager.get_instance()
        try:
            sm.create_user("linked_admin", "admin-password-123", Role.ADMIN)
            gw = AuthGatewayService({
                "providers": {},
                "admin_links": {
                    "google": {
                        "username": "linked_admin",
                        "claim": "email",
                        "value": "admin@example.com",
                    }
                },
            })
            result = AuthResult(
                success=True,
                provider="google",
                user_id="google-123",
                email="admin@example.com",
                username="external-admin",
            )

            user = gw._find_existing_user(sm, result)

            self.assertIsNotNone(user)
            self.assertEqual(user.username, "linked_admin")
            self.assertEqual(IdentityService.instance().resolve("google", "google-123"), "linked_admin")
        finally:
            IdentityService.reset()
            SecurityManager._instance = None

    def test_existing_oauth_only_user_matches_by_email_and_gets_linked(self):
        from core.identity_service import IdentityService
        from core.security import SecurityManager, Role
        from services.auth_gateway_service import AuthGatewayService

        IdentityService.reset()
        SecurityManager._instance = None
        sm = SecurityManager.get_instance()
        try:
            legacy_user = sm.create_user(
                "quentin.anciaux",
                "",
                Role.ADMIN,
                email="quentin.anciaux@allcolor.org",
                display_name="Quentin Anciaux",
            )
            legacy_user.password_hash = ""
            sm._save_users()
            gw = AuthGatewayService({"providers": {}})
            result = AuthResult(
                success=True,
                provider="google",
                user_id="google-sub-123",
                email="quentin.anciaux@allcolor.org",
                username="quentin.anciaux",
            )

            user = gw._find_existing_user(sm, result)

            self.assertIsNotNone(user)
            self.assertEqual(user.username, "quentin.anciaux")
            self.assertEqual(
                IdentityService.instance().resolve("google", "google-sub-123"),
                "quentin.anciaux",
            )
        finally:
            IdentityService.reset()
            SecurityManager._instance = None


class TestIdentityServiceResolve(unittest.TestCase):
    """Test IdentityService.resolve for account linking."""

    def setUp(self):
        from core.identity_service import IdentityService
        self.ids = IdentityService.instance()
        self.ids._mappings = {}  # Clear

    def test_resolve_linked(self):
        self.ids.link("alice", "google", "google:123")
        result = self.ids.resolve("google", "google:123")
        self.assertEqual(result, "alice")

    def test_resolve_not_linked(self):
        result = self.ids.resolve("google", "unknown:456")
        self.assertIsNone(result)

    def test_resolve_multiple_providers(self):
        self.ids.link("bob", "google", "google:111")
        self.ids.link("bob", "x", "x:222")
        self.assertEqual(self.ids.resolve("google", "google:111"), "bob")
        self.assertEqual(self.ids.resolve("x", "x:222"), "bob")

    def test_resolve_different_users(self):
        self.ids.link("alice", "google", "google:aaa")
        self.ids.link("bob", "google", "google:bbb")
        self.assertEqual(self.ids.resolve("google", "google:aaa"), "alice")
        self.assertEqual(self.ids.resolve("google", "google:bbb"), "bob")


def _isolated_auth_paths(tmp_path, monkeypatch):
    import core.paths as paths
    from core.identity_service import IdentityService
    from core.security import SecurityManager

    monkeypatch.setattr(paths, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(paths, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(paths, "SECURITY_FILE", tmp_path / "security.json")
    monkeypatch.setattr(paths, "USER_CONFIG_DIR", tmp_path / "users")
    monkeypatch.setattr(paths, "OAUTH_INVITE_TOKENS_FILE", tmp_path / "oauth_invite_tokens.json")
    SecurityManager._instance = None
    IdentityService.reset()


class _SuccessfulOAuthProvider:
    def __init__(self, *, provider="github", user_id="ext-1", username="external", email="ext@example.com"):
        self.provider = provider
        self.user_id = user_id
        self.username = username
        self.email = email

    def exchange_code(self, code, redirect_uri):
        return AuthResult(
            success=True,
            provider=self.provider,
            user_id=self.user_id,
            username=self.username,
            email=self.email,
            display_name="External User",
            claims={"email": self.email},
        )


class _SuccessfulTelegramProvider:
    def validate_telegram_data(self, data):
        assert data["id"] == "123"
        return AuthResult(
            success=True,
            provider="telegram",
            user_id="telegram:123",
            username="tg_user",
            display_name="Telegram User",
            claims={"telegram_id": "123"},
        )


def test_external_oauth_without_existing_user_requires_invite_token(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from services.auth_gateway_service import AuthGatewayService

    gw = AuthGatewayService({"providers": {}})
    gw._providers["github"] = _SuccessfulOAuthProvider()

    result = gw.authenticate_oauth("github", "code", "https://app.example/auth/callback")

    assert result.success is False
    assert "onboarding token" in result.error
    assert getattr(result, "pending_oauth_id", "")


def test_telegram_auth_without_existing_user_requires_invite_token(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from services.auth_gateway_service import AuthGatewayService

    gw = AuthGatewayService({"providers": {}})
    gw._providers["telegram"] = _SuccessfulTelegramProvider()

    result = gw.authenticate_telegram({"id": "123"})

    assert result.success is False
    assert "onboarding token" in result.error
    assert getattr(result, "pending_oauth_id", "")


def test_pending_oauth_can_complete_only_when_invite_token_exists(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from core import oauth_invite_tokens
    from services.auth_gateway_service import AuthGatewayService

    gw = AuthGatewayService({"providers": {}})
    gw._providers["github"] = _SuccessfulOAuthProvider()
    pending = gw.authenticate_oauth("github", "code", "https://app.example/auth/callback")
    pending_id = getattr(pending, "pending_oauth_id")

    assert gw.can_complete_pending_oauth(pending_id) is False

    oauth_invite_tokens.create_token(role="user", ttl_seconds=3600, created_by="admin")

    assert gw.can_complete_pending_oauth(pending_id) is True
    assert gw.can_complete_pending_oauth("missing") is False


def test_external_oauth_existing_username_or_email_still_requires_link_token(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from core.security import SecurityManager, Role
    from services.auth_gateway_service import AuthGatewayService

    sm = SecurityManager.get_instance()
    sm.create_user("external", "pass", Role.ADMIN, email="ext@example.com")
    gw = AuthGatewayService({"providers": {}})
    gw._providers["github"] = _SuccessfulOAuthProvider(
        user_id="gh-unlinked", username="external", email="ext@example.com")

    result = gw.authenticate_oauth("github", "code", "https://app.example/auth/callback")

    assert result.success is False
    assert "onboarding token" in result.error
    assert getattr(result, "pending_oauth_id", "")


def test_invite_token_creates_user_and_is_deleted_after_use(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from core import oauth_invite_tokens
    from core.security import SecurityManager
    from services.auth_gateway_service import AuthGatewayService

    gw = AuthGatewayService({"providers": {}})
    gw._providers["github"] = _SuccessfulOAuthProvider()
    pending = gw.authenticate_oauth("github", "code", "https://app.example/auth/callback")
    invite = oauth_invite_tokens.create_token(role="user", ttl_seconds=3600, created_by="admin")

    completed = gw.complete_pending_oauth(getattr(pending, "pending_oauth_id"), invite["token"])

    assert completed.success is True
    user = SecurityManager.get_instance().get_user("external")
    assert user is not None
    assert user.role.value == "user"
    assert oauth_invite_tokens.list_tokens() == []


def test_invite_token_links_existing_user_and_is_deleted_after_use(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from core import oauth_invite_tokens
    from core.identity_service import IdentityService
    from core.security import SecurityManager, Role
    from services.auth_gateway_service import AuthGatewayService

    sm = SecurityManager.get_instance()
    sm.create_user("alice", "pass", Role.USER, email="alice@example.com")
    gw = AuthGatewayService({"providers": {}})
    gw._providers["github"] = _SuccessfulOAuthProvider(username="ignored", user_id="gh-999")
    pending = gw.authenticate_oauth("github", "code", "https://app.example/auth/callback")
    invite = oauth_invite_tokens.create_token(
        role="user", link_username="alice", ttl_seconds=3600, created_by="admin")

    completed = gw.complete_pending_oauth(getattr(pending, "pending_oauth_id"), invite["token"])

    assert completed.success is True
    assert completed.username == "alice"
    assert IdentityService.instance().resolve("github", "gh-999") == "alice"
    assert oauth_invite_tokens.list_tokens() == []


def test_oauth_invite_tokens_delete_on_expiry_and_revoke(tmp_path, monkeypatch):
    _isolated_auth_paths(tmp_path, monkeypatch)
    from core import oauth_invite_tokens

    short = oauth_invite_tokens.create_token(role="user", ttl_seconds=60, created_by="admin")
    active = oauth_invite_tokens.create_token(role="user", ttl_seconds=3600, created_by="admin")
    monkeypatch.setattr(oauth_invite_tokens.time, "time", lambda: short["expires_at"] + 1)

    assert [t["id"] for t in oauth_invite_tokens.list_tokens()] == [active["id"]]
    assert oauth_invite_tokens.revoke_token(active["id"]) is True
    assert oauth_invite_tokens.list_tokens() == []


if __name__ == "__main__":
    unittest.main()
