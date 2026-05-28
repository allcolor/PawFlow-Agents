#!/usr/bin/env python3
"""Generate PawFlow relay Docker images from declarative feature profiles."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "config" / "relay_image_catalog.json"


def _load_catalog(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if "features" not in data or "profiles" not in data:
        raise ValueError("relay image catalog must define features and profiles")
    return data


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _resolve_features(catalog: dict[str, Any], profile: str, extra: list[str]) -> list[str]:
    features = catalog["features"]
    selected: list[str] = []
    for feature_id in catalog.get("required_features", []):
        selected.append(feature_id)
    if profile:
        profiles = catalog["profiles"]
        if profile not in profiles:
            raise ValueError(f"unknown relay image profile: {profile}")
        selected.extend(profiles[profile].get("features", []))
    selected.extend(extra)

    resolved: list[str] = []
    visiting: set[str] = set()

    def visit(feature_id: str) -> None:
        if feature_id in resolved:
            return
        if feature_id not in features:
            raise ValueError(f"unknown relay image feature: {feature_id}")
        if feature_id in visiting:
            raise ValueError(f"cyclic relay image feature dependency at {feature_id}")
        visiting.add(feature_id)
        for dep in features[feature_id].get("implies", []):
            visit(dep)
        visiting.remove(feature_id)
        resolved.append(feature_id)

    for feature_id in _dedupe(selected):
        visit(feature_id)
    return resolved


def _collect(catalog: dict[str, Any], feature_ids: list[str], key: str) -> list[str]:
    out: list[str] = []
    for feature_id in feature_ids:
        value = catalog["features"][feature_id].get(key, [])
        if isinstance(value, list):
            out.extend(str(v) for v in value)
    return _dedupe(out)


def _collect_env(catalog: dict[str, Any], feature_ids: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    path_prefixes: list[str] = []
    for feature_id in feature_ids:
        for key, value in catalog["features"][feature_id].get("env", {}).items():
            if key == "PATH_PREFIX":
                path_prefixes.extend(str(value).split(":"))
            else:
                env[key] = str(value)
    if path_prefixes:
        env["PATH"] = ":".join(_dedupe(path_prefixes)) + ":" + "$" + "{PATH}"
    return env


def _render_run(commands: list[str], indent: str = "    ") -> list[str]:
    if not commands:
        return []
    if len(commands) == 1:
        return [f"RUN {commands[0]}"]
    lines = ["RUN " + commands[0] + " \\"]
    for command in commands[1:-1]:
        lines.append(f"{indent}&& {command} \\")
    lines.append(f"{indent}&& {commands[-1]}")
    return lines


def _render_apt(packages: list[str]) -> list[str]:
    if not packages:
        return []
    lines = ["RUN apt-get update && apt-get install -y --no-install-recommends \\"]
    for chunk in [packages[i:i + 6] for i in range(0, len(packages), 6)]:
        lines.append("    " + " ".join(chunk) + " \\")
    lines.append("    && rm -rf /var/lib/apt/lists/*")
    return lines


def _render_pkg_install(tool: str, packages: list[str]) -> list[str]:
    if not packages:
        return []
    quoted = " ".join(shlex.quote(pkg) for pkg in packages)
    if tool == "pip":
        return [f"RUN pip3 install --break-system-packages --no-cache-dir {quoted}"]
    if tool == "npm":
        return [f"RUN npm install -g {quoted}"]
    if tool == "gem":
        return [f"RUN gem install {quoted}"]
    raise ValueError(f"unknown package tool: {tool}")


def _render_user_install(commands: list[str]) -> list[str]:
    if not commands:
        return []
    return ["USER pawflow", *_render_run(commands), "USER root"]


def _render_dockerfile(catalog: dict[str, Any], feature_ids: list[str], image_name: str) -> str:
    required_ids = [f for f in catalog.get("required_features", []) if f in feature_ids]
    optional_ids = [f for f in feature_ids if f not in set(required_ids)]
    required_apt = _collect(catalog, required_ids, "apt")
    optional_apt = _collect(catalog, optional_ids, "apt")
    pre_apt = _collect(catalog, feature_ids, "pre_apt")
    setup = _collect(catalog, feature_ids, "setup")
    post_install = _collect(catalog, feature_ids, "post_install")
    user_install = _collect(catalog, feature_ids, "user_install")
    pip = _collect(catalog, feature_ids, "pip")
    npm = _collect(catalog, feature_ids, "npm")
    gem = _collect(catalog, feature_ids, "gem")
    env = _collect_env(catalog, feature_ids)

    lines: list[str] = [
        "# Generated by scripts/generate-relay-image.py. Do not edit generated output by hand.",
        "FROM ubuntu:24.04",
        "",
        "ENV DEBIAN_FRONTEND=noninteractive",
        "ENV LANG=C.UTF-8",
        "ENV LC_ALL=C.UTF-8",
        "ENV PATH=\"/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\"",
        "",
        "RUN apt-get update && apt-get install -y --no-install-recommends sudo \\",
        "    && rm -rf /var/lib/apt/lists/* \\",
        "    && if id -u ubuntu >/dev/null 2>&1; then \\",
        "           usermod -l pawflow -d /home/pawflow -m ubuntu \\",
        "        && groupmod -n pawflow ubuntu \\",
        "        && usermod -aG sudo pawflow; \\",
        "       else \\",
        "           groupadd -g 1000 pawflow \\",
        "        && useradd -u 1000 -g 1000 -m -s /bin/bash -G sudo pawflow; \\",
        "       fi \\",
        "    && echo 'pawflow ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/pawflow \\",
        "    && chmod 440 /etc/sudoers.d/pawflow",
        "",
    ]
    lines.extend(_render_apt(required_apt))
    lines.append("")
    lines.extend(_render_run(pre_apt))
    if pre_apt:
        lines.append("")
    lines.extend(_render_apt(optional_apt))
    if optional_apt:
        lines.append("")
    lines.extend(_render_run(setup))
    if setup:
        lines.append("")
    lines.extend(_render_pkg_install("pip", pip))
    lines.extend(_render_pkg_install("npm", npm))
    lines.extend(_render_pkg_install("gem", gem))
    if pip or npm or gem:
        lines.append("")
    lines.extend(_render_user_install(user_install))
    if user_install:
        lines.append("")
    lines.extend(_render_run(post_install))
    if post_install:
        lines.append("")

    for key, value in env.items():
        lines.append(f"ENV {key}=\"{value}\"")
    lines.extend([
        f"ENV PAWFLOW_DOCKER_IMAGE=\"{image_name}\"",
        "COPY runtime/ /opt/pawflow/",
        "RUN apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*",
        "RUN chown -R pawflow:pawflow /opt/pawflow /home/pawflow \\",
        "    && find /home/pawflow -maxdepth 1 ! -user pawflow -exec chown -R pawflow:pawflow {} +",
        "USER pawflow",
        "ENV HOME=\"/home/pawflow\"",
        "ENV USER=\"pawflow\"",
        "WORKDIR /workspace",
        "ENTRYPOINT [\"/usr/local/bin/init.sh\"]",
        "CMD [\"bash\"]",
        "",
    ])
    return "\n".join(lines)


def _manifest(catalog: dict[str, Any], profile: str, feature_ids: list[str], image_name: str) -> dict[str, Any]:
    features = catalog["features"]
    total_size = sum(int(features[f].get("estimated_size_mb", 0)) for f in feature_ids)
    runtime_args: list[str] = []
    for feature_id in feature_ids:
        runtime_args.extend(features[feature_id].get("runtime", {}).get("docker_args", []))
    return {
        "version": catalog.get("version", 1),
        "profile": profile,
        "image": image_name,
        "features": feature_ids,
        "categories": sorted({features[f].get("category", "other") for f in feature_ids}),
        "estimated_size_mb": total_size,
        "runtime_docker_args": _dedupe(runtime_args),
    }


def _copy_runtime_files(out_dir: Path) -> None:
    runtime_dir = out_dir / "runtime"
    if runtime_dir.exists():
        shutil.rmtree(runtime_dir)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for src in [
        ROOT / "tools" / "pawflow_relay_launcher.py",
        ROOT / "tools" / "fs_actions.py",
        ROOT / "tools" / "fs_exec.py",
        ROOT / "tools" / "fs_screen.py",
        ROOT / "tools" / "fs_mcp.py",
        ROOT / "tools" / "fs_common.py",
        ROOT / "tools" / "fs_http.py",
        ROOT / "tools" / "audio_capture.py",
        ROOT / "tools" / "screen_actions.py",
        ROOT / "docker" / "pawflow_sdk" / "pawflow.py",
    ]:
        if not src.exists():
            raise FileNotFoundError(f"required relay runtime file missing: {src}")
        shutil.copy2(src, runtime_dir / src.name)
    shutil.copytree(ROOT / "pawflow_relay", runtime_dir / "pawflow_relay")


def _write_scripts(out_dir: Path, image_name: str, manifest: dict[str, Any]) -> None:
    dollar = "$"
    image_expansion = dollar + "{PAWFLOW_RELAY_IMAGE:-" + image_name + "}"
    workspace_expansion = dollar + "{PAWFLOW_RELAY_WORKSPACE:-" + dollar + "PWD}"
    build = out_dir / "build.sh"
    build.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
        f"IMAGE={image_expansion}\n"
        "PLATFORM=\"${PAWFLOW_DOCKER_PLATFORM:-}\"\n"
        "BUILD_ARGS=()\n"
        "if [[ -n \"$PLATFORM\" ]]; then BUILD_ARGS+=(--platform \"$PLATFORM\"); fi\n"
        "docker build \"${BUILD_ARGS[@]}\" -t \"$IMAGE\" \"$SCRIPT_DIR\"\n",
        encoding="utf-8",
    )
    run_args = " ".join(shlex.quote(arg) for arg in manifest.get("runtime_docker_args", []))
    run = out_dir / "run-relay.sh"
    run.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f": {dollar}{{PAWFLOW_SERVER:?Set PAWFLOW_SERVER, for example wss://host:PORT/ws/relay}}\n"
        f": {dollar}{{PAWFLOW_RELAY_TOKEN:?Set PAWFLOW_RELAY_TOKEN from the PawFlow relay installer}}\n"
        f": {dollar}{{PAWFLOW_RELAY_ID:?Set PAWFLOW_RELAY_ID from the PawFlow relay installer}}\n"
        f"WORKSPACE={workspace_expansion}\n"
        f"IMAGE={image_expansion}\n"
        "docker run --rm --name \"pawflow-relay-$PAWFLOW_RELAY_ID\" \\\n"
        "  -v \"$WORKSPACE:/workspace\" \\\n"
        "  -v \"pawflow_home_$PAWFLOW_RELAY_ID:/home/pawflow\" \\\n"
        f"  {run_args} \\\n"
        "  \"$IMAGE\" python3 /opt/pawflow/pawflow_relay_launcher.py \\\n"
        "  --server \"$PAWFLOW_SERVER\" --token \"$PAWFLOW_RELAY_TOKEN\" \\\n"
        "  --relay-id \"$PAWFLOW_RELAY_ID\" --dir /workspace --allow-exec \\\n"
        "  --server-mount /cc_sessions --filestore-mount /filestore "
        "--skills-mount /skills\n",
        encoding="utf-8",
    )
    os.chmod(build, 0o755)
    os.chmod(run, 0o755)


def generate(catalog_path: Path, profile: str, extra_features: list[str], out_dir: Path, image_name: str) -> dict[str, Any]:
    catalog = _load_catalog(catalog_path)
    feature_ids = _resolve_features(catalog, profile, extra_features)
    out_dir.mkdir(parents=True, exist_ok=True)
    _copy_runtime_files(out_dir)
    dockerfile = _render_dockerfile(catalog, feature_ids, image_name)
    (out_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    manifest = _manifest(catalog, profile, feature_ids, image_name)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _write_scripts(out_dir, image_name, manifest)
    return manifest


def _print_catalog(catalog: dict[str, Any]) -> None:
    print("Profiles:")
    for profile_id, profile in sorted(catalog["profiles"].items()):
        print(f"  {profile_id}: {profile.get('label', profile_id)}")
    print("\nFeatures:")
    for feature_id, feature in sorted(catalog["features"].items()):
        marker = " required" if feature.get("required") else ""
        print(f"  {feature_id}: {feature.get('label', feature_id)} [{feature.get('category', 'other')}]{marker}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--profile", default="client-minimal")
    parser.add_argument("--feature", action="append", default=[], help="Additional feature id to include. May be repeated.")
    parser.add_argument("--out", type=Path, default=ROOT / "docker" / "relay-generated" / "client-minimal")
    parser.add_argument("--image", default="pawflow-relay:client-minimal")
    parser.add_argument("--list", action="store_true", help="List profiles and features, then exit.")
    args = parser.parse_args()

    catalog = _load_catalog(args.catalog)
    if args.list:
        _print_catalog(catalog)
        return 0
    manifest = generate(args.catalog, args.profile, args.feature, args.out, args.image)
    print(f"Generated {args.out}")
    print(f"Image: {manifest['image']}")
    print(f"Features: {', '.join(manifest['features'])}")
    print(f"Estimated size: {manifest['estimated_size_mb']} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
