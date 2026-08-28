from pathlib import Path
import json
import subprocess


ROOT = Path("pawflow-desktop")


def test_pawflow_desktop_is_a_secure_separate_chat_application():
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    main = (ROOT / "src/main.js").read_text(encoding="utf-8")
    preload = (ROOT / "src/preload.js").read_text(encoding="utf-8")
    tabs = (ROOT / "src/tab_manager.js").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert package["name"] == "pawflow-desktop"
    assert package["build"]["appId"] == "org.allcolor.pawflow.desktop"
    assert set(package["build"]["protocols"][0]["schemes"]) == {"pawflow"}
    assert {"dist:win", "dist:linux", "dist:mac"} <= set(package["scripts"])
    assert "contextIsolation: true" in main
    assert "sandbox: true" in main
    assert "nodeIntegration: false" in main
    assert "contextBridge.exposeInMainWorld" in preload
    assert "X-PawFlow-Gateway-Key" not in preload
    assert "setPermissionRequestHandler" in tabs
    assert "sameOrigin(profile.base_url" in tabs
    assert "relay lifecycle" not in preload.lower()
    assert "never creates, starts, stops, or owns relays" in readme


def test_pawflow_desktop_uses_mobile_pkce_and_os_protected_secrets():
    auth = (ROOT / "src/auth.js").read_text(encoding="utf-8")
    main = (ROOT / "src/main.js").read_text(encoding="utf-8")
    profiles = (ROOT / "src/profile_store.js").read_text(encoding="utf-8")
    pkce = (ROOT / "src/pkce.js").read_text(encoding="utf-8")

    for route in (
        "/auth/mobile/providers",
        "/auth/mobile/builtin",
        "/auth/mobile/start",
    ):
        assert route in auth
    assert "/auth/mobile/consume" in (ROOT / "src/tab_manager.js").read_text(
        encoding="utf-8")
    assert "createPkce()" in main
    assert "randomBytes(64)" in pkce
    assert "safeStorage.encryptString" in profiles
    assert "safeStorage.decryptString" in profiles
    assert "basic_text" in profiles
    assert "Gateway key is required" in profiles


def test_pawflow_desktop_node_sources_and_unit_tests_pass():
    subprocess.run(
        ["npm", "run", "check"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["npm", "test"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


def test_pawflow_desktop_documentation_and_plan_exist():
    plan = Path("docs/DESKTOP_CLIENT_PLAN.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "PawFlow Native Desktop Client Implementation Plan" in plan
    assert "OS-protected" in plan
    assert "OAuth 2.0 using PKCE" in plan
    assert "PawFlow Desktop" in readme
