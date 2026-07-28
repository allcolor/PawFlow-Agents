"""Structural tests for the conversation-sharing UI (phase 7).

The repo has no JS runner, so these pin the wiring a reviewer would
otherwise have to re-verify by hand: that the sidebar asks for the shared
list, that invites are a two-step accept, that the share dialog is
owner-only, that key parity holds across the three locales, and that the
module is actually served.
"""

import json
from pathlib import Path

UI = Path("tasks/io/chat_ui")
SHARE_JS = (UI / "conversations_share.js").read_text(encoding="utf-8")
CONVERSATIONS_JS = (UI / "conversations.js").read_text(encoding="utf-8")
MENU_JS = (UI / "conversations_menu.js").read_text(encoding="utf-8")
MESSAGES_RENDER_JS = (UI / "messages_render.js").read_text(encoding="utf-8")
TEMPLATE_HTML = (UI / "template.html").read_text(encoding="utf-8")
SERVE = Path("tasks/io/serve_chat_ui.py").read_text(encoding="utf-8")


def _locale(name):
    return json.loads((UI / "i18n" / f"{name}.json").read_text(encoding="utf-8"))


def test_the_share_module_is_served_before_the_sidebar_core():
    # renderConvList calls _convGroupHeader/renderSharedSections, both defined
    # in the share module: served after the core, the sidebar throws on its
    # first render.
    assert '"conversations_share.js"' in SERVE
    assert SERVE.index('"conversations_share.js"') < SERVE.index('"conversations.js"')


def test_the_sidebar_asks_for_the_shared_list():
    assert "list_shared_conversations" in SHARE_JS
    assert "loadSharedConversations" in CONVERSATIONS_JS
    assert "renderSharedSections(list)" in CONVERSATIONS_JS


def test_the_shared_list_does_not_block_the_users_own_conversations():
    # Two independent subscriptions, not one nested in the other: a slow
    # shared-list call must not hold the whole sidebar empty.
    loader = CONVERSATIONS_JS[
        CONVERSATIONS_JS.index("function loadConversations()"):
        CONVERSATIONS_JS.index("function _convRuntimeStatus")]
    assert loader.count("action$(") == 1          # the own-list call
    assert "loadSharedConversations(" in loader   # the shared one, separate


def test_the_empty_state_accounts_for_shared_conversations():
    # A user whose only conversations are shared with them must not be told
    # they have none.
    render = CONVERSATIONS_JS[
        CONVERSATIONS_JS.index("function renderConvList"):
        CONVERSATIONS_JS.index("function escapeAttr")]
    assert "convs.length === 0 && shared.length === 0" in render


def test_an_invite_is_accepted_or_declined_explicitly():
    # The invite row grants nothing on its own: both branches go through
    # respond_to_share_invite, which is what flips the server-side status.
    assert "respond_to_share_invite" in SHARE_JS
    assert "'accept'" in SHARE_JS and "'decline'" in SHARE_JS
    assert "conv-invite-accept" in SHARE_JS and "conv-invite-decline" in SHARE_JS


def test_the_share_dialog_refuses_to_open_for_a_non_owner():
    dialog = SHARE_JS[SHARE_JS.index("function showShareDialog"):
                      SHARE_JS.index("function _collaboratorRow")]
    assert "data.role !== 'owner'" in dialog
    assert "shareOwnerOnly" in dialog


def test_the_share_dialog_hides_kicked_rows_but_the_server_keeps_them():
    # Kicked rows stay in the ACL for the audit trail; showing them as
    # collaborators would misreport who has access.
    assert "r.status !== 'kicked'" in SHARE_JS


def test_the_author_badge_compares_against_the_viewer():
    # It must render on someone else's message and never on one's own, so
    # the comparison is against the viewer's id, which the server now sends.
    badge = SHARE_JS[SHARE_JS.index("function _authorBadgeHtml"):
                     SHARE_JS.index("// ── Sidebar rendering")]
    assert "author === window._userId" in badge
    assert "extra.source.type === 'user'" in badge
    assert "_authorBadgeHtml(extra)" in MESSAGES_RENDER_JS
    assert "window._userId = data.user_id" in (
        UI / "resources_render.js").read_text(encoding="utf-8")
    assert '"user_id"] = user_id' in Path(
        "tasks/ai/actions/_agentres_k3.py").read_text(encoding="utf-8")


def test_the_context_menus_offer_share_to_owners_and_leave_to_collaborators():
    assert "showShareDialog(cid)" in MENU_JS
    assert "leaveSharedConv" in SHARE_JS
    # A collaborator gets no delete entry: deletion is owner-only.
    shared_menu = SHARE_JS[SHARE_JS.index("function showSharedConvMenu"):]
    assert "deleteConversationById" not in shared_menu


def test_the_new_styles_exist():
    for cls in (".conv-group", ".conv-invite-actions", ".conv-role-badge",
                ".msg-author"):
        assert cls in TEMPLATE_HTML


def test_locale_key_parity_for_the_sharing_keys():
    en, fr, es = _locale("en"), _locale("fr"), _locale("es")
    assert set(en) == set(fr) == set(es)
    for key in ("acceptInvite", "declineInvite", "invite", "invitedBy",
                "inviteAccepted", "inviteDeclined", "kickCollaborator",
                "kickCollaboratorConfirm", "leaveConversation",
                "leaveConversationConfirm", "leftConversation",
                "myConversations", "noCollaborators", "ownedBy",
                "pendingInvites", "role.read", "role.write", "sharedWithMe",
                "shareConversation", "shareEncryptedWarning",
                "shareInviteHint", "shareOwnerOnly", "shareUserPlaceholder",
                "sharedMessageFrom", "status.accepted", "status.kicked",
                "status.pending"):
        for locale in (en, fr, es):
            assert locale.get(key), f"missing translation for {key}"


def test_every_translation_key_used_by_the_share_module_exists():
    import re
    en = _locale("en")
    # The lookbehind keeps createElement('div') out of the match.
    used = set(re.findall(r"(?<![A-Za-z0-9_.])t\('([A-Za-z0-9_.]+)'", SHARE_JS))
    # Role and status labels are built by concatenation, checked above.
    used = {k for k in used if not k.endswith(".")}
    dynamic = {"role.", "status."}
    missing = {k for k in used
               if k not in en and not any(k.startswith(d) for d in dynamic)}
    assert missing == set()
