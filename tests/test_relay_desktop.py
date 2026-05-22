from pathlib import Path
import json
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "pawflow-relay-desktop"


def test_relay_desktop_package_exposes_electron_app():
    package = json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))
    assert package["main"] == "src/main.js"
    assert package["scripts"]["start"] == "electron ."
    assert package["scripts"]["prepare-runtime"] == "node scripts/prepare-runtime.js"
    assert package["scripts"]["build-relay-bin"] == "node scripts/build-relay-bin.js"
    assert package["scripts"]["package:portable"] == "node scripts/package-portable.js"
    assert "electron-builder" in package["scripts"]["dist"]
    assert package["build"]["win"]["target"] == ["nsis"]
    assert "AppImage" in package["build"]["linux"]["target"]
    assert "dmg" in package["build"]["mac"]["target"]
    assert {"from": "runtime", "to": "runtime"} in package["build"]["extraResources"]
    assert "scripts/prepare-runtime.js" in package["scripts"]["check"]
    assert "scripts/build-relay-bin.js" in package["scripts"]["check"]
    assert "scripts/package-portable.js" in package["scripts"]["check"]
    assert "electron" in package["devDependencies"]
    assert "electron-builder" in package["devDependencies"]


def test_relay_desktop_uses_python_manager_and_safe_preload():
    main = (DESKTOP / "src" / "main.js").read_text(encoding="utf-8")
    preload = (DESKTOP / "src" / "preload.js").read_text(encoding="utf-8")
    renderer = (DESKTOP / "src" / "renderer.js").read_text(encoding="utf-8")

    assert "Tray" in main
    assert "Menu" in main
    assert "nativeImage" in main
    assert "assets', 'tray-icon.png'" in main
    assert "nativeImage.createFromPath" in main
    assert (DESKTOP / "src" / "assets" / "tray-icon.png").is_file()
    assert "function createTray()" in main
    assert "function refreshTrayMenu()" in main
    assert "function cleanupRelayRuntime(name)" in main
    assert "async function stopRelay(name)" in main
    assert "async function stopAllRelays()" in main
    assert "async function quitApp()" in main
    assert "runRelayClientJson(['cleanup', name || ''])" in main
    assert "process.platform === 'win32' ? 'SIGTERM' : 'SIGINT'" in main
    assert "await stopAllRelays()" in main
    assert "win.hide()" in main
    assert "before-quit" in main
    assert "Open PawFlow Relay" in main
    assert "Relays" in main
    assert "Servers" in main
    assert "Start" in main
    assert "Stop" in main
    assert "Login" in main
    assert "showOpenDialog" in main
    assert "relay:select-directory" in main
    assert "relay:docker-images" in main
    assert "function runDocker(" in main
    assert "function runDockerBuild(" in main
    assert "function relayBinaryPath()" in main
    assert "function relayClientCommand(" in main
    assert "function runRelayClientJson(" in main
    assert "PAWFLOW_RELAY_BIN" in main
    assert "process.resourcesPath, 'runtime'" in main
    assert "'bin', relayBinaryName()" in main
    assert "function wslPath(" in main
    assert "PAWFLOW_RELAY_WSL_DISTRO" in main
    assert "wsl.exe" in main
    assert "wslpath" in main
    assert "return { images, error: '' }" in main
    assert "return { images: [], error: message }" in main
    assert "relay:image-catalog" in main
    assert "relay:build-image" in main
    assert "docker builder prune" not in main
    assert "builder', 'prune', '-f'" in main
    assert "nodeIntegration: false" in main
    assert "contextIsolation: true" in main
    assert "process.platform === 'win32' ? 'python' : 'python3'" in main
    assert "process.platform === 'win32' ? 'py' : 'python3'" not in main
    assert "function runtimeRoot()" in main
    assert "PAWFLOW_RELAY_RUNTIME_ROOT" in main
    assert "roots.join(path.delimiter)" in main
    assert "runRelayClientJson(['status'])" in main
    assert "python -m pawflow_relay" not in main
    assert "['-m', 'pawflow_relay', ...extraArgs]" in main
    assert "relayClientCommand(['start', name])" in main
    assert "contextBridge.exposeInMainWorld('pawflowRelay'" in preload
    assert "ipcRenderer.invoke('relay:add-server'" in preload
    assert "ipcRenderer.invoke('relay:delete-server'" in preload
    assert "ipcRenderer.invoke('relay:delete-workspace'" in preload
    assert "ipcRenderer.invoke('relay:select-directory'" in preload
    assert "ipcRenderer.invoke('relay:docker-images'" in preload
    assert "ipcRenderer.invoke('relay:image-catalog'" in preload
    assert "ipcRenderer.invoke('relay:build-image'" in preload
    assert "relay:delete-server" in main
    assert "relay:delete-workspace" in main
    assert "window.pawflowRelay.addWorkspace" in renderer
    assert "window.pawflowRelay.start" in renderer
    assert "window.pawflowRelay.deleteServer" in renderer
    assert "window.pawflowRelay.deleteWorkspace" in renderer
    assert "cancelServerBtn" in renderer
    assert "cancelWorkspaceBtn" in renderer
    assert "browsePathBtn" in renderer
    assert "buildImageBtn" in renderer
    assert "Build Relay Image" in renderer
    assert "window.pawflowRelay.buildRelayImage" in renderer
    assert "dockerError" in renderer
    assert "Docker unavailable" in renderer
    assert "selectDirectory" in renderer
    assert "allowExec" in renderer
    assert "allowRemoteDesktop" in renderer
    assert "Allow local access" in renderer
    assert "showContextMenu" in renderer
    assert "serverTree" in renderer
    assert "workspaceTree" in renderer


def test_relay_manager_exposes_workspace_runtime_cleanup():
    manager = (ROOT / "pawflow_relay" / "manager.py").read_text(encoding="utf-8")
    thread = (ROOT / "pawflow_relay" / "thread.py").read_text(encoding="utf-8")

    assert "def stop_workspace_runtime" in manager
    assert "service_uninstall" in manager
    assert "cleanup_relay_containers(relay_id)" in manager
    assert "def cleanup_relay_containers" in thread
    assert "finally:" in manager
    assert "relay.stop()" in manager


def test_relay_desktop_uses_webchat_style_tree_shell():
    index = (DESKTOP / "src" / "index.html").read_text(encoding="utf-8")
    css = (DESKTOP / "src" / "styles.css").read_text(encoding="utf-8")

    assert "serverTree" in index
    assert "workspaceTree" in index
    assert "detailPanel" in index
    assert "contextMenu" in index
    assert ".sidebar" in css
    assert ".tree-item" in css
    assert ".card" in css
    assert ".toggle-grid" in css
    assert ".button.ghost" in css


def test_relay_desktop_prepare_runtime_script_declares_required_payload():
    script = (DESKTOP / "scripts" / "prepare-runtime.js").read_text(encoding="utf-8")
    assert "pawflow_relay_launcher.py" in script
    assert "fs_actions.py" in script
    assert "screen_actions.py" in script
    assert "docker', 'pawflow_sdk', 'pawflow.py'" in script
    assert "config', 'relay_image_catalog.json'" in script
    assert "scripts', 'generate-relay-image.py'" in script
    assert "copyDir(path.join(repoRoot, 'pawflow_relay')" in script
    assert "copyDir(path.join(repoRoot, 'pawflow_cli')" in script
    assert "__pycache__" in script
    portable = (DESKTOP / "scripts" / "package-portable.js").read_text(encoding="utf-8")
    assert "prepare-runtime.js" in portable
    assert "dist', 'pawflow-relay-desktop'" in portable
    assert "node_modules" in portable
    build_bin = (DESKTOP / "scripts" / "build-relay-bin.js").read_text(encoding="utf-8")
    assert "PyInstaller" in build_bin
    assert "relay-bin-entry.py" in build_bin
    assert "runtimeRoot" in build_bin
    assert "binDir" in build_bin
    assert "pawflow-relay.exe" in build_bin
    assert "--hidden-import" in build_bin


def test_relay_manager_cli_supports_desktop_json_contract():
    cli = (ROOT / "pawflow_relay" / "manager_cli.py").read_text(encoding="utf-8")
    main = (ROOT / "pawflow_relay" / "__main__.py").read_text(encoding="utf-8")

    assert 'parser.add_argument("--json"' in cli
    assert "delete_server" in cli
    assert "delete_workspace" in cli
    assert "stop_workspace_runtime" in cli
    assert "cleanup" in cli
    assert '"cleanup"' in main
    assert "def _first_command" in main
    assert 'if arg == "--json"' in main


def test_relay_desktop_generated_runtime_has_required_payload():
    subprocess.run(["npm", "run", "prepare-runtime"], cwd=DESKTOP, check=True)
    runtime = DESKTOP / "runtime"
    assert (runtime / "tools" / "pawflow_relay_launcher.py").is_file()
    assert (runtime / "tools" / "fs_actions.py").is_file()
    assert (runtime / "tools" / "screen_actions.py").is_file()
    assert (runtime / "docker" / "pawflow_sdk" / "pawflow.py").is_file()
    assert (runtime / "config" / "relay_image_catalog.json").is_file()
    assert (runtime / "scripts" / "generate-relay-image.py").is_file()
    assert (runtime / "pawflow_relay" / "thread.py").is_file()
    assert (runtime / "pawflow_cli" / "auth.py").is_file()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime)
    subprocess.run(
        [sys.executable, "-c", "import pawflow_cli.auth; import pawflow_relay.manager_cli"],
        cwd=DESKTOP,
        env=env,
        check=True,
    )


def test_relay_client_doc_mentions_desktop_app():
    doc = (ROOT / "docs" / "relay_client.md").read_text(encoding="utf-8")
    readme = (DESKTOP / "README.md").read_text(encoding="utf-8")
    assert "pawflow-relay-desktop/" in doc
    assert "npm start" in doc
    assert "npm run package:portable" in readme
    assert "npm run dist:linux" in readme
    assert "runtime/bin/pawflow-relay" in readme
    assert "Cannot create symbolic link" in readme
    assert "CSC_IDENTITY_AUTO_DISCOVERY" in readme
    assert "Electron Builder" in doc
    assert "winCodeSign" in doc
    assert "runtime/tools/" in readme
    assert "PAWFLOW_RELAY_WSL_DISTRO" in readme
