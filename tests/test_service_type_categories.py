from tasks.ai.actions.service_flow import _service_category, _service_type_sort_key


class _ImageService:
    CATEGORY = "image"


class _LegacyService:
    pass


class _UnknownCategoryService:
    CATEGORY = "unsupported"


class _TryOnService:
    CATEGORY = "try_on"


def test_service_category_prefers_declared_category():
    assert _service_category("customImage", _ImageService) == "image"


def test_service_category_maps_legacy_service_type():
    assert _service_category("rcloneFilesystem", _LegacyService) == "filesystem"


def test_service_category_normalizes_catalog_aliases():
    assert _service_category("pixazoTryOn", _TryOnService) == "try-on"


def test_service_category_falls_back_to_other():
    assert _service_category("customService", _UnknownCategoryService) == "other"


def test_service_type_sort_key_orders_by_category_then_name():
    services = [
        {"type": "rcloneFilesystem", "name": "Rclone", "category": "filesystem"},
        {"type": "authGateway", "name": "Auth Gateway", "category": "auth"},
        {"type": "llmConnection", "name": "LLM Connection", "category": "ai"},
    ]

    assert [svc["type"] for svc in sorted(services, key=_service_type_sort_key)] == [
        "authGateway",
        "llmConnection",
        "rcloneFilesystem",
    ]
