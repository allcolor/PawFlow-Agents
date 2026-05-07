from services import private_gateway


def test_bladerunner_private_gateway_skin_renders(monkeypatch):
    from core import expression

    monkeypatch.setattr(
        expression,
        "_load_global_parameters",
        lambda: {"gateway_skin": "bladerunner"},
    )

    html = private_gateway.render_challenge(
        error="Denied", cooldown=3, next_url="/chat?x=1&y=2",
    ).decode("utf-8")

    assert "Blade Runner Gateway" in html
    assert "Private Gateway" in html
    assert "Voight-Kampff code" in html
    assert "Signal locked. Retry in " in html
    assert "Denied" in html
    assert "/chat?x=1&amp;y=2" in html
