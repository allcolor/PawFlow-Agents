import json

from pawflow_installer.frontends.cli import main


def test_cli_plan_is_read_only_and_emits_stable_json(capsys):
    result = main([
        "plan",
        "--target", "local",
        "--pawflow-home", "/srv/pawflow",
        "--port", "9443",
        "--source", "published",
        "--reachability", "local",
        "--json",
    ])
    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["request"]["target"]["kind"] == "local"
    assert payload["steps"][0]["step_id"] == "request_validated"
    assert any(step["mutating"] for step in payload["steps"])


def test_cli_rejects_missing_required_values(capsys):
    assert main(["plan", "--target", "local", "--json"]) == 1
    assert "missing required options" in capsys.readouterr().err
