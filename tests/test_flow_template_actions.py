import json

from core import FlowFile


def _write_flow_template(root, package="default", flow_name="demo", version="1.0.0"):
    flow_dir = root / package.replace(".", "/") / flow_name
    versions_dir = flow_dir / "versions"
    versions_dir.mkdir(parents=True)
    (flow_dir / "latest.json").write_text(
        json.dumps({"version": version}) + "\n", encoding="utf-8")
    (versions_dir / f"{version}.json").write_text(json.dumps({
        "id": flow_name,
        "name": flow_name,
        "version": version,
        "package": package,
        "fqn": f"{package}.{flow_name}:{version}",
        "tasks": {},
        "relations": [],
        "services": {},
    }) + "\n", encoding="utf-8")
    return flow_dir


def _payload(flowfile):
    return json.loads(flowfile.get_content().decode("utf-8"))


def test_move_flow_template_package_creates_package_and_updates_fqn(tmp_path, monkeypatch):
    import core.paths as paths
    from tasks.ai.actions.service_flow import _handle_service_flow

    monkeypatch.setattr(paths, "REPOSITORY_DIR", tmp_path / "repository")
    root = paths.REPOSITORY_DIR / "flows" / "users" / "alice"
    old_dir = _write_flow_template(root)

    ff = FlowFile(content=b"")
    _handle_service_flow(
        object(),
        "move_flow_template_package",
        {"template_id": "demo", "package": "custom.pkg"},
        None,
        "alice",
        ff,
    )

    assert _payload(ff)["ok"] is True
    assert not old_dir.exists()
    new_dir = root / "custom" / "pkg" / "demo"
    assert new_dir.is_dir()
    assert (root / "custom" / "pkg" / "package.json").is_file()
    raw = json.loads((new_dir / "versions" / "1.0.0.json").read_text(encoding="utf-8"))
    assert raw["package"] == "custom.pkg"
    assert raw["fqn"] == "custom.pkg.demo:1.0.0"
