from core.handlers._fs_base import BaseFsHandler


def test_parse_absolute_filestore_path_routes_to_server_filestore():
    svc, path = BaseFsHandler._parse_fs_url(
        "/filestore/83333a72ddee4ed5/cef267e1fda8/screenshot_1777634185.png"
    )

    assert svc == "filestore"
    assert path == "83333a72ddee4ed5/cef267e1fda8/screenshot_1777634185.png"
    assert BaseFsHandler._filestore_id_from_path(path) == "cef267e1fda8"


def test_filestore_id_from_canonical_url():
    assert (
        BaseFsHandler._filestore_id_from_path("fs://filestore/cef267e1fda8/screenshot.png")
        == "cef267e1fda8"
    )
