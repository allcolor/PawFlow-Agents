"""IdP group claims -> PawFlow roles.

Authentication is the identity provider's job and PawFlow does not redo it.
What was missing is the other half: the groups Keycloak already manages were
fetched with the userinfo response and thrown away, so authorisation fell back
to a role stored locally and edited by hand.

Three rules hold it together, and each test below is one way of getting them
wrong:

1. an unmapped group grants NOTHING -- authority is the operator's mapping
   table, never the group's name;
2. local wins by default -- making the IdP authoritative is a choice;
3. group names never reach `http.auth.roles`, because ~29 call sites test that
   attribute with `"admin" in roles`, a SUBSTRING test.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from core.auth_groups import (
    PRECEDENCE_LOCAL, PRECEDENCE_REMOTE, claim_groups, highest_role,
    map_groups, resolve_role, role_precedence)


class TheClaim(unittest.TestCase):

    def test_a_dotted_path_reaches_a_nested_claim(self):
        """Keycloak puts realm roles in realm_access.roles.

        A flat .get() finds nothing there -- silently, which is the worst way
        for an authorisation lookup to fail.
        """
        claims = {"realm_access": {"roles": ["pawflow-admins", "offline_access"]}}
        self.assertEqual(claim_groups(claims, "realm_access.roles"),
                         ["pawflow-admins", "offline_access"])

    def test_a_flat_claim_still_works(self):
        self.assertEqual(claim_groups({"groups": ["a", "b"]}, "groups"), ["a", "b"])

    def test_a_space_or_comma_separated_string_is_accepted(self):
        """Providers disagree on the shape; all three appear in the wild."""
        self.assertEqual(claim_groups({"groups": "a b"}, "groups"), ["a", "b"])
        self.assertEqual(claim_groups({"groups": "a, b"}, "groups"), ["a", "b"])

    def test_a_missing_claim_is_no_groups_not_an_error(self):
        for path, claims in (("groups", {}),
                             ("realm_access.roles", {"realm_access": {}}),
                             ("a.b.c", {"a": "scalar"}),
                             ("", {"groups": ["x"]})):
            self.assertEqual(claim_groups(claims, path), [], path)


class TheMapping(unittest.TestCase):

    def test_an_unmapped_group_grants_nothing(self):
        """Otherwise creating a group named `admin` in the IdP is an escalation."""
        self.assertEqual(map_groups(["admin", "wheel", "root"], {}), [])
        self.assertEqual(
            map_groups(["admin"], {"pawflow-admins": "admin"}), [])

    def test_a_mapped_group_grants_the_mapped_role(self):
        self.assertEqual(
            map_groups(["pawflow-admins"], {"pawflow-admins": "admin"}),
            ["admin"])

    def test_a_mapping_to_an_unknown_role_grants_nothing(self):
        self.assertEqual(map_groups(["g"], {"g": "superuser"}), [])

    def test_the_most_privileged_mapped_role_wins(self):
        """"Member of pawflow-admins" is the natural reading.

        The opposite would let a broad group cancel a privileged one.
        """
        self.assertEqual(highest_role(["user", "admin"]), "admin")
        self.assertEqual(highest_role(["user"]), "user")
        self.assertEqual(highest_role([]), "")


class ThePrecedence(unittest.TestCase):

    def test_local_wins_by_default(self):
        self.assertEqual(resolve_role("user", "admin", PRECEDENCE_LOCAL), "user")

    def test_remote_wins_when_asked(self):
        self.assertEqual(resolve_role("user", "admin", PRECEDENCE_REMOTE), "admin")

    def test_no_mapped_role_leaves_the_stored_one_alone_in_both_modes(self):
        """Deliberate asymmetry, and the reason it exists.

        A strict "remote wins" would mean a forgotten scope, or a client that
        stopped emitting the claim, demotes every user at once -- one
        misconfiguration away from locking an instance out of its own admin.
        """
        for mode in (PRECEDENCE_LOCAL, PRECEDENCE_REMOTE):
            self.assertEqual(resolve_role("admin", "", mode), "admin", mode)

    def test_a_brand_new_user_takes_the_mapped_role(self):
        """No stored value is not a conflict."""
        self.assertEqual(resolve_role("", "admin", PRECEDENCE_LOCAL), "admin")

    def test_an_unknown_precedence_falls_back_to_local(self):
        """A typo in a security setting must not make the IdP authoritative."""
        with patch("core.expression._load_global_parameters",
                   return_value={"auth.role_precedence": "REMOTE_PLZ"}):
            self.assertEqual(role_precedence(), PRECEDENCE_LOCAL)

    def test_the_parameter_is_read_from_global_parameters(self):
        with patch("core.expression._load_global_parameters",
                   return_value={"auth.role_precedence": "remote"}):
            self.assertEqual(role_precedence(), PRECEDENCE_REMOTE)
        with patch("core.expression._load_global_parameters", return_value={}):
            self.assertEqual(role_precedence(), PRECEDENCE_LOCAL)

    def test_an_unreadable_parameter_store_falls_back_to_local(self):
        with patch("core.expression._load_global_parameters",
                   side_effect=OSError("no file")):
            self.assertEqual(role_precedence(), PRECEDENCE_LOCAL)


class TheLastAdmin(unittest.TestCase):
    """A group-mapping edit must not be able to lock everyone out.

    SecurityManager.update_user already refuses this, but the login path does
    not go through it -- and once the last admin is demoted there is no route
    left in the UI to undo it.
    """

    def _sm(self, role_value, admin_count):
        class _User:
            enabled = True

            def __init__(self, r):
                from core.security import Role
                self.role = Role(r)

        class _SM:
            def get_user(self, _u):
                return _User(role_value)

            def _enabled_admin_count(self):
                return admin_count

        return _SM()

    def test_demoting_the_last_admin_is_refused(self):
        from core import auth_groups
        with patch("core.security.SecurityManager.get_instance",
                   return_value=self._sm("admin", 1)):
            self.assertTrue(auth_groups.would_orphan_last_admin("root", "user"))

    def test_demoting_one_admin_among_several_is_allowed(self):
        from core import auth_groups
        with patch("core.security.SecurityManager.get_instance",
                   return_value=self._sm("admin", 2)):
            self.assertFalse(auth_groups.would_orphan_last_admin("root", "user"))

    def test_promoting_is_never_an_orphan_risk(self):
        from core import auth_groups
        self.assertFalse(auth_groups.would_orphan_last_admin("anyone", "admin"))

    def test_an_unanswerable_check_keeps_the_admin(self):
        """Refusing a demotion is recoverable; locking everyone out is not."""
        from core import auth_groups
        with patch("core.security.SecurityManager.get_instance",
                   side_effect=RuntimeError("store down")):
            self.assertTrue(auth_groups.would_orphan_last_admin("root", "user"))


class GroupsNeverBecomeRoles(unittest.TestCase):
    """Rule 3, and the reason the whole feature is safe."""

    def test_the_admin_gate_matches_exactly_not_as_a_substring(self):
        from core.admin_scope import is_admin

        class _FF:
            def __init__(self, roles):
                self._roles = roles

            def get_attribute(self, _name):
                return self._roles

        self.assertTrue(is_admin(_FF("admin")))
        self.assertTrue(is_admin(_FF("user,admin")))
        self.assertFalse(is_admin(_FF("user")))
        # These are the ones a substring test would have accepted.
        self.assertFalse(is_admin(_FF("admin-readonly")))
        self.assertFalse(is_admin(_FF("non-admin")))
        self.assertFalse(is_admin(_FF("badmin")))

    def test_groups_travel_in_their_own_attribute(self):
        for path in ("tasks/io/validate_session_auth.py",
                     "tasks/io/oauth_callback.py"):
            src = Path(path).read_text(encoding="utf-8")
            self.assertIn('"http.auth.groups"', src, path)

    def test_a_session_written_before_groups_existed_still_loads(self):
        """Sessions are persisted and reloaded; old rows have no key."""
        from core.security import Role, Session
        session = Session(session_id="s", username="u", role=Role.USER)
        self.assertEqual(session.groups, [])


if __name__ == "__main__":
    unittest.main()
