from pathlib import Path


def test_router_candidate_editor_is_structured_and_not_raw_json():
    source = Path(
        "tasks/io/chat_ui/schema_form.js").read_text(
            encoding="utf-8")
    assert "ptype === 'service_ref_list'" in source
    assert "data-service-ref-list" in source
    assert "data-candidate-service" in source
    assert "data-candidate-priority" in source
    assert "data-candidate-enabled" in source
    assert "ondragstart" in source and "ondrop" in source
    assert "config[pname] = Array.from(el.querySelectorAll('.svc-ref-list-row'))" in source
