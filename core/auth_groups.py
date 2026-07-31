"""IdP group claims -> PawFlow roles.

Authentication is the identity provider's job and PawFlow does not try to do it
again: no LDAP client, no directory sync. What was missing is the other half --
the groups Keycloak (or Okta, or Auth0) already manages were fetched with the
userinfo response and then thrown away, so authorisation fell back to a role
stored locally and edited by hand.

Three rules hold this together, and each exists because its opposite is a way
to hand out admin by accident:

1. **An unmapped group grants nothing.** Authority comes from the mapping an
   operator wrote in PawFlow, never from the group's name. Otherwise creating a
   group called `admin` in the IdP would be a privilege escalation.

2. **Local wins by default** (``auth.role_precedence``). The stored role is the
   one PawFlow can see and change; making the IdP authoritative is a deliberate
   choice, not the default one.

3. **Group names never reach ``http.auth.roles``.** That attribute keeps
   carrying resolved PawFlow roles only (`admin` / `user`), because ~29 call
   sites test it with ``"admin" in roles`` -- a SUBSTRING test, which a group
   named `admin-readonly` or `non-admin` would satisfy. Groups travel in
   ``http.auth.groups`` instead. Keeping the two apart is what makes those call
   sites safe without rewriting every one of them.
"""

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Ordering used when several mapped groups resolve to different roles.
#: Highest wins: "member of pawflow-admins" is the natural reading, and the
#: opposite would let a broad group silently cancel a privileged one.
ROLE_RANK = {"user": 1, "admin": 2}

PRECEDENCE_LOCAL = "local"
PRECEDENCE_REMOTE = "remote"
PRECEDENCE_PARAM = "auth.role_precedence"


def claim_groups(claims: Dict[str, Any], path: str) -> List[str]:
    """Group names at a dotted ``path`` inside the claims.

    The path is dotted because the interesting claim usually is: Keycloak puts
    realm roles in ``realm_access.roles``, and a flat ``.get()`` finds nothing
    there -- silently, which is the worst way for an authorisation lookup to
    fail.

    Accepts a list, or a single string, or a space/comma separated string,
    because providers disagree and all three appear in the wild.
    """
    if not path:
        return []
    node: Any = claims or {}
    for part in path.split("."):
        if not isinstance(node, dict):
            return []
        node = node.get(part)
        if node is None:
            return []
    if isinstance(node, str):
        node = node.replace(",", " ").split()
    if not isinstance(node, (list, tuple, set)):
        return []
    return [str(item).strip() for item in node if str(item).strip()]


def map_groups(groups: Sequence[str], mappings: Dict[str, str]) -> List[str]:
    """The PawFlow roles a set of IdP groups grants, deduplicated.

    Exact match on the group name. A group with no entry in ``mappings``
    grants nothing at all -- see rule 1 in the module docstring.
    """
    if not groups or not mappings:
        return []
    out: List[str] = []
    for group in groups:
        role = str(mappings.get(group, "") or "").strip().lower()
        if role and role in ROLE_RANK and role not in out:
            out.append(role)
    return out


def highest_role(roles: Sequence[str]) -> str:
    """The most privileged of several mapped roles, or "" for none."""
    ranked = [r for r in roles if r in ROLE_RANK]
    if not ranked:
        return ""
    return max(ranked, key=lambda r: ROLE_RANK[r])


def role_precedence() -> str:
    """Who wins when the stored role and the mapped role disagree.

    ``local`` (the default) or ``remote``, from ``global_parameters.json``.
    Anything unrecognised falls back to ``local``: a typo in a security
    setting must not quietly make the IdP authoritative.
    """
    try:
        from core.expression import _load_global_parameters
        raw = str(_load_global_parameters().get(PRECEDENCE_PARAM, "") or "").strip().lower()
    except Exception:
        logger.debug("auth: role precedence lookup failed", exc_info=True)
        return PRECEDENCE_LOCAL
    if raw == PRECEDENCE_REMOTE:
        return PRECEDENCE_REMOTE
    if raw and raw != PRECEDENCE_LOCAL:
        logger.warning(
            "auth: unknown %s=%r; keeping %r", PRECEDENCE_PARAM, raw,
            PRECEDENCE_LOCAL)
    return PRECEDENCE_LOCAL


def resolve_role(stored_role: str, mapped_role: str,
                 precedence: Optional[str] = None) -> str:
    """The effective role for a login, given both sources.

    Two asymmetries, both deliberate:

    * No mapped role means the stored one stands, in BOTH modes. A strict
      "remote wins" would mean that a forgotten scope, or a Keycloak client
      that stopped emitting the claim, demotes every user at once -- one
      misconfiguration away from locking an instance out of its own admin.
    * ``local`` keeps the stored role even when the mapping would raise it,
      so an operator can demote somebody in PawFlow without editing the IdP.
    """
    stored = (stored_role or "").strip().lower()
    mapped = (mapped_role or "").strip().lower()
    if not mapped:
        return stored
    if not stored:
        return mapped
    mode = precedence or role_precedence()
    return mapped if mode == PRECEDENCE_REMOTE else stored


def would_orphan_last_admin(username: str, new_role: str) -> bool:
    """True when applying ``new_role`` would leave no enabled admin.

    ``SecurityManager.update_user`` already refuses this, but the login path
    does not go through it. Without the same guard, a single edit to a group
    mapping in the IdP can demote the last admin -- and there is then no route
    left in the UI to undo it.
    """
    if (new_role or "").strip().lower() == "admin":
        return False
    try:
        from core.security import Role, SecurityManager
        sm = SecurityManager.get_instance()
        user = sm.get_user(username)
        if not user or not user.enabled or user.role != Role.ADMIN:
            return False
        return sm._enabled_admin_count() <= 1
    except Exception:
        # Unable to tell -> assume it would, and keep the admin. Refusing a
        # demotion is recoverable; locking everyone out is not.
        logger.warning("auth: last-admin check failed; keeping admin role",
                       exc_info=True)
        return True
