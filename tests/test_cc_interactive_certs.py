from pathlib import Path


def test_leaf_generation_keeps_ca_private_key_host_only(tmp_path, monkeypatch):
    import core.cc_interactive_certs as certs

    monkeypatch.setattr(certs, "CA_CERT", tmp_path / "cc_interactive_ca.crt")
    monkeypatch.setattr(certs, "CA_KEY", tmp_path / "cc_interactive_ca.key")

    leaf = certs.generate_leaf(tmp_path / "session")

    assert leaf.cert_path.exists()
    assert leaf.key_path.exists()
    assert leaf.ca_cert_path.exists()
    assert certs.CA_KEY.exists()
    assert certs.ca_private_key_is_host_only([leaf.cert_path, leaf.key_path, leaf.ca_cert_path]) is True
    assert certs.ca_private_key_is_host_only([Path(certs.CA_KEY)]) is False
