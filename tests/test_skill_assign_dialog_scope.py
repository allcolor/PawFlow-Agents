"""The skill-assign dialog must offer only conversation agent instances.

A skill is assigned to an *instance*: it lands in that instance's
`assigned_skills` inside the conversation. `assign_skill_to_agent` therefore
refuses any name that is not in the conversation, so a dialog listing
repository definitions produced choices that were guaranteed to fail --
reported from the UI as agents from other conversations appearing in the list.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MENUS = ROOT / "tasks" / "io" / "chat_ui" / "resources_menus.js"
I18N = ROOT / "tasks" / "io" / "chat_ui" / "i18n"


def _dialog_source() -> str:
    src = MENUS.read_text(encoding="utf-8")
    start = src.index("function _showSkillAssignDialog")
    end = src.index("\nfunction ", start + 1)
    return src[start:end]


class TestDialogScope:

    def test_repo_agents_are_not_offered(self):
        """The regression itself: repo definitions must not reach the select."""
        assert "repo_agents" not in _dialog_source()

    def test_conversation_agents_are_the_source(self):
        assert "data.agents" in _dialog_source()

    def test_empty_roster_explains_why(self):
        """An empty list must say the conversation has no agent, not 'none exist'."""
        assert "noConvAgentsForSkill" in _dialog_source()


class TestMessageIsTranslated:

    def test_key_present_in_every_locale(self):
        for name in ("en.json", "fr.json", "es.json"):
            catalogue = json.loads((I18N / name).read_text(encoding="utf-8"))
            assert catalogue["noConvAgentsForSkill"].strip()

    def test_locales_stay_in_key_parity(self):
        keys = {
            name: set(json.loads((I18N / name).read_text(encoding="utf-8")))
            for name in ("en.json", "fr.json", "es.json")
        }
        assert keys["en.json"] == keys["fr.json"] == keys["es.json"]


class TestServerRefusesOutsiders:
    """The contract the dialog now matches."""

    def test_assign_requires_an_instance_in_the_conversation(self):
        source = (ROOT / "core" / "skill_lifecycle.py").read_text(encoding="utf-8")
        assign = source[source.index("def assign_skill_to_agent"):]
        assert re.search(r"not found in conversation", assign)

    def test_assign_requires_a_conversation_id(self):
        source = (ROOT / "core" / "skill_lifecycle.py").read_text(encoding="utf-8")
        assign = source[source.index("def assign_skill_to_agent"):]
        assert "Missing conversation_id" in assign
