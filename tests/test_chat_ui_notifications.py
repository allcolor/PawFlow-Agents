"""Runtime notification-center contracts for the web chat."""

import json
from pathlib import Path


CHAT_UI = Path("tasks/io/chat_ui")
TEMPLATE = (CHAT_UI / "template.html").read_text(encoding="utf-8")
NOTIFICATIONS = (CHAT_UI / "notifications.js").read_text(encoding="utf-8")
MESSAGES = (CHAT_UI / "messages_render.js").read_text(encoding="utf-8")
TYPING = (CHAT_UI / "typing.js").read_text(encoding="utf-8")
SSE_A = (CHAT_UI / "sse_handlers_a.js").read_text(encoding="utf-8")
SSE_B = (CHAT_UI / "sse_handlers_b.js").read_text(encoding="utf-8")
HELP = (CHAT_UI / "cmd_misc.js").read_text(encoding="utf-8")


def test_notification_button_precedes_linked_accounts_in_header():
    header = TEMPLATE[
        TEMPLATE.index('<div class="header">'):
        TEMPLATE.index('id="actionMenuWrap"')
    ]
    assert header.index('id="notificationCenterBtn"') < header.index(
        'id="linkAccountBtn"'
    )
    assert 'id="notificationCenterBadge"' in header
    assert 'onclick="openNotificationCenter()"' in header
    assert "#pf-notif-stack" in TEMPLATE
    assert "#notificationCenterDialog .pf-notif-dialog" in TEMPLATE


def test_notification_history_is_tab_runtime_only():
    assert "var _pfNotifications = [];" in NOTIFICATIONS
    assert "function showUiNotification(" in NOTIFICATIONS
    assert "function openNotificationCenter(" in NOTIFICATIONS
    assert "function clearRuntimeNotifications(" in NOTIFICATIONS
    assert "function removeUiNotificationByKey(" in NOTIFICATIONS
    assert "indexedDB" not in NOTIFICATIONS
    assert "action$(" not in NOTIFICATIONS
    assert "fetch(" not in NOTIFICATIONS
    # localStorage is limited to the pre-existing mute preference, never entries.
    assert NOTIFICATIONS.count("localStorage.setItem(") == 1
    assert "pawflow.notif.muted" in NOTIFICATIONS


def test_notification_sse_event_has_one_normalizing_listener():
    assert (SSE_A + SSE_B).count(
        "addEventListener('notification'"
    ) == 1
    assert "handleSseNotification(data);" in SSE_A
    assert "data.content || data.message" in NOTIFICATIONS
    assert "data.agent || data.agent_name" in NOTIFICATIONS
    assert "addMsg('system', urgencyIcon" not in SSE_B


def test_client_notices_leave_persisted_messages_in_transcript():
    assert "const _runtimeNotice = (role === 'system' || role === 'error')" in MESSAGES
    assert "&& !msgId" in MESSAGES
    assert "extra.raw_index !== undefined" in MESSAGES
    assert "extra.transcript" in MESSAGES
    assert "return showUiNotification(text" in MESSAGES
    # Legacy persisted notification rows are ignored, not replayed into runtime.
    assert "extra.source.name === 'notification'" in MESSAGES
    assert "notification-row" not in MESSAGES


def test_context_progress_and_help_do_not_append_chat_rows():
    context_section = TYPING[TYPING.index("function showContextOp"):]
    assert "showUiNotification(label" in context_section
    assert "key: 'context-operation'" in context_section
    assert "removeUiNotificationByKey('context-operation')" in context_section
    assert "contextOpTyping" not in context_section

    help_section = HELP[:HELP.index("function cmdUsage")]
    assert "openCenter: true" in help_section
    assert "detailHtml:" in help_section
    assert "addMsg('system', '')" not in help_section

    sse_state = (CHAT_UI / "sse_state.js").read_text(encoding="utf-8")
    assert "key: 'service-install:' + key" in sse_state
    assert "row.textContent = text" not in sse_state


def test_notification_i18n_keys_exist_in_every_catalog():
    required = {
        "notifications",
        "notificationsDesc",
        "notificationsUnread",
        "notificationCenterEmpty",
        "clearNotifications",
        "dismissNotification",
        "notificationDetails",
        "notificationInfo",
        "notificationSuccess",
        "notificationWarning",
        "notificationError",
        "notificationProgress",
        "notificationTestMessage",
    }
    catalogs = []
    for language in ("en", "fr", "es"):
        catalogs.append(json.loads(
            (CHAT_UI / "i18n" / f"{language}.json").read_text(encoding="utf-8")
        ))
    assert all(required <= catalog.keys() for catalog in catalogs)
    assert all(set(catalog) == set(catalogs[0]) for catalog in catalogs)


def test_push_notification_handler_is_sse_only():
    handler = Path("core/handlers/push_notification.py").read_text(
        encoding="utf-8"
    )
    budget = Path("core/budget_store.py").read_text(encoding="utf-8")
    handler_delivery = handler[handler.index("# Runtime-only delivery"):]
    budget_delivery = budget[budget.index("def _notify("):]

    assert "ConversationEventBus.instance().publish_event" in handler_delivery
    assert "ConversationWriter" not in handler_delivery
    assert '"new_message"' not in handler_delivery
    assert "bus.publish_event(conversation_id, \"notification\"" in budget_delivery
    assert "ConversationWriter" not in budget_delivery
