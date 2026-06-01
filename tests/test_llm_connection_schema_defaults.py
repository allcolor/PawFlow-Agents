def test_llm_connection_compact_defaults_are_proactive():
    from services.llm_connection import LLMConnectionService

    schema = LLMConnectionService({}).get_parameter_schema()

    assert schema["compact_target_tokens"]["default"] == 25000
    assert schema["compact_threshold_pct"]["default"] == 95
