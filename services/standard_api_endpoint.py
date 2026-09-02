"""Public routes for standard OpenAI and Anthropic agent APIs."""

from __future__ import annotations

import json
import logging
import secrets
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.mcp_server_endpoint import _json_response


logger = logging.getLogger(__name__)
_ROUTE_OWNER = "_published_standard_agent_api"
_NOT_FOUND_MESSAGE = "The requested standard API surface was not found."
_MAX_REQUEST_BYTES = 2_000_000
_OPENAI_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
_ANTHROPIC_SSE_HEADERS = dict(_OPENAI_SSE_HEADERS)


def _request_id() -> str:
    return "req_" + secrets.token_urlsafe(18).replace("-", "").replace("_", "")


def _openai_error(
        req,
        status: int,
        *,
        message: str,
        error_type: str,
        code: str,
        param: Optional[str] = None,
        request_id: str = "",
        headers: Optional[Dict[str, str]] = None,
) -> None:
    request_id = request_id or _request_id()
    response_headers = dict(headers or {})
    response_headers["x-request-id"] = request_id
    _json_response(req, status, {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }, response_headers)


def _anthropic_error(
        req,
        status: int,
        *,
        message: str,
        error_type: str,
        request_id: str = "",
) -> None:
    request_id = request_id or _request_id()
    _json_response(req, status, {
        "type": "error",
        "error": {
            "type": error_type,
            "message": message,
        },
        "request_id": request_id,
    }, {"request-id": request_id})


def _openai_not_found(req) -> None:
    _openai_error(
        req,
        404,
        message=_NOT_FOUND_MESSAGE,
        error_type="invalid_request_error",
        code="not_found",
    )


def _anthropic_not_found(req) -> None:
    _anthropic_error(
        req,
        404,
        message=_NOT_FOUND_MESSAGE,
        error_type="not_found_error",
    )


def _header_value(req, name: str) -> str:
    headers = getattr(req, "headers", None) or {}
    needle = name.lower()
    for key, value in headers.items():
        if str(key).lower() == needle:
            return str(value or "").strip()
    return ""


def _anthropic_access(req):
    from core.standard_api_config import (
        DIALECT_AVAILABILITY,
        DIALECT_FIELD_NAMES,
    )
    from services.published_agent_auth import resolve_published_agent

    credential = _header_value(req, "x-api-key")
    access = (
        resolve_published_agent(req, credential=credential)
        if credential
        else resolve_published_agent(req)
    )
    if access.error == "unauthorized":
        _anthropic_error(
            req,
            401,
            message="Invalid API key.",
            error_type="authentication_error",
        )
        return None
    if access.error == "origin_forbidden":
        _anthropic_error(
            req,
            403,
            message="The request origin is not allowed.",
            error_type="permission_error",
        )
        return None
    if access.error:
        _anthropic_not_found(req)
        return None
    publication = access.publication
    if (
            not publication.get("enabled")
            or publication.get("context_policy") != "isolated"
            or not publication.get("standard_api_enabled")
            or not DIALECT_AVAILABILITY.get("anthropic_messages")
            or not publication.get(
                DIALECT_FIELD_NAMES["anthropic_messages"])
    ):
        _anthropic_not_found(req)
        return None
    return publication, access.key


def _anthropic_version_headers(req, request_id: str) -> bool:
    from core.standard_api_contract import (
        ANTHROPIC_SUPPORTED_BETAS,
        ANTHROPIC_SUPPORTED_VERSIONS,
    )

    version = _header_value(req, "anthropic-version")
    if version not in ANTHROPIC_SUPPORTED_VERSIONS:
        _anthropic_error(
            req,
            400,
            message=(
                "anthropic-version must name a supported API version; "
                "supported: " + ", ".join(sorted(ANTHROPIC_SUPPORTED_VERSIONS))
            ),
            error_type="invalid_request_error",
            request_id=request_id,
        )
        return False
    raw_beta = _header_value(req, "anthropic-beta")
    betas = {
        item.strip()
        for item in raw_beta.split(",")
        if item.strip()
    }
    unsupported = sorted(betas - set(ANTHROPIC_SUPPORTED_BETAS))
    if unsupported:
        _anthropic_error(
            req,
            400,
            message=(
                "Unsupported anthropic-beta value: " + unsupported[0]
            ),
            error_type="invalid_request_error",
            request_id=request_id,
        )
        return False
    return True


def _anthropic_request_object(
        req,
        request_id: str,
) -> Optional[Dict[str, Any]]:
    body = getattr(req, "body", b"") or b""
    if len(body) > _MAX_REQUEST_BYTES:
        _anthropic_error(
            req,
            413,
            message="The request body exceeds the size limit.",
            error_type="invalid_request_error",
            request_id=request_id,
        )
        return None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _anthropic_error(
            req,
            400,
            message="The request body must be valid JSON.",
            error_type="invalid_request_error",
            request_id=request_id,
        )
        return None
    if not isinstance(value, dict):
        _anthropic_error(
            req,
            400,
            message="The request body must be a JSON object.",
            error_type="invalid_request_error",
            request_id=request_id,
        )
        return None
    return value


def _openai_access(req, dialect: str):
    from core.standard_api_config import (
        DIALECT_AVAILABILITY,
        DIALECT_FIELD_NAMES,
    )
    from services.published_agent_auth import resolve_published_agent

    access = resolve_published_agent(req)
    if access.error == "unauthorized":
        _openai_error(
            req,
            401,
            message="Incorrect API key provided.",
            error_type="invalid_request_error",
            code="invalid_api_key",
            headers={"WWW-Authenticate": "Bearer"},
        )
        return None
    if access.error == "origin_forbidden":
        _openai_error(
            req,
            403,
            message="The request origin is not allowed.",
            error_type="invalid_request_error",
            code="forbidden",
        )
        return None
    if access.error:
        _openai_not_found(req)
        return None
    publication = access.publication
    if (
            not publication.get("enabled")
            or publication.get("context_policy") != "isolated"
            or not publication.get("standard_api_enabled")
            or not DIALECT_AVAILABILITY.get(dialect)
            or not publication.get(DIALECT_FIELD_NAMES[dialect])
    ):
        _openai_not_found(req)
        return None
    return publication, access.key


def _openai_models_access(req):
    from core.standard_api_config import DIALECT_AVAILABILITY
    from services.published_agent_auth import resolve_published_agent

    access = resolve_published_agent(req)
    if access.error == "unauthorized":
        _openai_error(
            req,
            401,
            message="Incorrect API key provided.",
            error_type="invalid_request_error",
            code="invalid_api_key",
            headers={"WWW-Authenticate": "Bearer"},
        )
        return None
    if access.error == "origin_forbidden":
        _openai_error(
            req,
            403,
            message="The request origin is not allowed.",
            error_type="invalid_request_error",
            code="forbidden",
        )
        return None
    if access.error:
        _openai_not_found(req)
        return None
    publication = access.publication
    openai_enabled = (
        (
            DIALECT_AVAILABILITY["chat_completions"]
            and publication.get("api_chat_completions_enabled")
        )
        or (
            DIALECT_AVAILABILITY["responses"]
            and publication.get("api_responses_enabled")
        )
    )
    if (
            not publication.get("enabled")
            or publication.get("context_policy") != "isolated"
            or not publication.get("standard_api_enabled")
            or not openai_enabled
    ):
        _openai_not_found(req)
        return None
    return publication, access.key


def _openai_model(publication: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": publication["api_model_id"],
        "object": "model",
        "created": int(float(publication.get("created_at") or 0)),
        "owned_by": "pawflow",
    }


def _request_object(req, request_id: str) -> Optional[Dict[str, Any]]:
    body = getattr(req, "body", b"") or b""
    if len(body) > _MAX_REQUEST_BYTES:
        _openai_error(
            req,
            413,
            message="The request body exceeds the size limit.",
            error_type="invalid_request_error",
            code="request_too_large",
            request_id=request_id,
        )
        return None
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _openai_error(
            req,
            400,
            message="The request body must be valid JSON.",
            error_type="invalid_request_error",
            code="invalid_json",
            request_id=request_id,
        )
        return None
    if not isinstance(value, dict):
        _openai_error(
            req,
            400,
            message="The request body must be a JSON object.",
            error_type="invalid_request_error",
            code="invalid_request",
            request_id=request_id,
        )
        return None
    return value


def handle_openai_models(req) -> None:
    access = _openai_models_access(req)
    if access is None:
        return
    publication, _key = access
    _json_response(req, 200, {
        "object": "list",
        "data": [_openai_model(publication)],
    }, {"x-request-id": _request_id()})


def handle_openai_model(req) -> None:
    access = _openai_models_access(req)
    if access is None:
        return
    publication, _key = access
    model_id = str(
        (getattr(req, "path_params", None) or {}).get("model_id") or "")
    if model_id != publication["api_model_id"]:
        _openai_error(
            req,
            404,
            message="The requested model does not exist.",
            error_type="invalid_request_error",
            code="model_not_found",
            param="model",
        )
        return
    _json_response(
        req, 200, _openai_model(publication),
        {"x-request-id": _request_id()})


def handle_openai_chat_completions(req) -> None:
    access = _openai_access(req, "chat_completions")
    if access is None:
        return
    publication, key = access
    request_id = _request_id()
    body = _request_object(req, request_id)
    if body is None:
        return
    from core.a2a_store import A2AStore
    from core.standard_api_chat import (
        OpenAIChatError,
        OpenAIChatRunError,
        iter_openai_chat_sse,
        prepare_openai_chat_run,
        wait_openai_chat_payload,
    )
    try:
        admission = prepare_openai_chat_run(
            A2AStore.instance(),
            publication,
            key,
            body,
            request_id=request_id,
        )
    except OpenAIChatError as exc:
        _openai_error(
            req,
            exc.status,
            message=str(exc),
            error_type=exc.error_type,
            code=exc.code,
            param=exc.param,
            request_id=request_id,
        )
        return
    except Exception:
        logger.exception("OpenAI Chat admission failed")
        _openai_error(
            req,
            500,
            message="The published agent request failed.",
            error_type="server_error",
            code="internal_error",
            request_id=request_id,
        )
        return

    if admission.run.turn.stream:
        headers = dict(_OPENAI_SSE_HEADERS)
        headers["x-request-id"] = request_id
        req.complete_stream(200, headers, iter_openai_chat_sse(admission))
        return

    def _complete_non_stream() -> None:
        try:
            payload = wait_openai_chat_payload(admission.run)
        except OpenAIChatRunError as exc:
            _openai_error(
                req,
                exc.status,
                message=str(exc),
                error_type="server_error",
                code=exc.code,
                request_id=request_id,
            )
        except Exception:
            logger.exception("OpenAI Chat response failed")
            _openai_error(
                req,
                500,
                message="The published agent request failed.",
                error_type="server_error",
                code="internal_error",
                request_id=request_id,
            )
        else:
            _json_response(
                req, 200, payload, {"x-request-id": request_id})

    threading.Thread(
        target=_complete_non_stream,
        daemon=True,
        name=f"openai-chat-response-{request_id[:16]}",
    ).start()


def handle_openai_responses(req) -> None:
    access = _openai_access(req, "responses")
    if access is None:
        return
    publication, key = access
    request_id = _request_id()
    body = _request_object(req, request_id)
    if body is None:
        return
    from core.a2a_store import A2AStore
    from core.standard_api_responses import (
        OpenAIResponsesError,
        OpenAIResponsesRunError,
        iter_openai_response_sse,
        prepare_openai_response_run,
        wait_openai_response_payload,
    )
    try:
        admission = prepare_openai_response_run(
            A2AStore.instance(),
            publication,
            key,
            body,
            request_id=request_id,
        )
    except OpenAIResponsesError as exc:
        _openai_error(
            req,
            exc.status,
            message=str(exc),
            error_type=exc.error_type,
            code=exc.code,
            param=exc.param,
            request_id=request_id,
        )
        return
    except Exception:
        logger.exception("OpenAI Responses admission failed")
        _openai_error(
            req,
            500,
            message="The published agent request failed.",
            error_type="server_error",
            code="internal_error",
            request_id=request_id,
        )
        return

    if admission.run.turn.stream:
        headers = dict(_OPENAI_SSE_HEADERS)
        headers["x-request-id"] = request_id
        req.complete_stream(
            200, headers, iter_openai_response_sse(admission))
        return

    def _complete_non_stream() -> None:
        try:
            payload = wait_openai_response_payload(admission.run)
        except OpenAIResponsesRunError as exc:
            _openai_error(
                req,
                exc.status,
                message=str(exc),
                error_type="server_error",
                code=exc.code,
                request_id=request_id,
            )
        except Exception:
            logger.exception("OpenAI Responses response failed")
            _openai_error(
                req,
                500,
                message="The published agent request failed.",
                error_type="server_error",
                code="internal_error",
                request_id=request_id,
            )
        else:
            _json_response(
                req, 200, payload, {"x-request-id": request_id})

    threading.Thread(
        target=_complete_non_stream,
        daemon=True,
        name=f"openai-responses-response-{request_id[:16]}",
    ).start()


def handle_openai_response_get(req) -> None:
    access = _openai_access(req, "responses")
    if access is None:
        return
    publication, key = access
    request_id = _request_id()
    response_id = str(
        (getattr(req, "path_params", None) or {}).get("response_id") or "")
    from core.a2a_store import A2AStore
    from core.standard_api_types import StandardApiNamespace

    namespace = StandardApiNamespace(
        publication_id=str(publication["publication_id"]),
        api_generation=int(publication["api_generation"]),
        key_id=str(key["key_id"]),
        dialect="responses",
        api_model_id=str(publication["api_model_id"]),
    )
    response = A2AStore.instance().get_api_response(
        namespace, response_id)
    if response is None:
        _openai_error(
            req,
            404,
            message=_NOT_FOUND_MESSAGE,
            error_type="invalid_request_error",
            code="not_found",
            request_id=request_id,
        )
        return
    _json_response(
        req, 200, response["envelope"], {"x-request-id": request_id})


def handle_openai_response_delete(req) -> None:
    access = _openai_access(req, "responses")
    if access is None:
        return
    publication, key = access
    request_id = _request_id()
    response_id = str(
        (getattr(req, "path_params", None) or {}).get("response_id") or "")
    from core.a2a_store import A2AStore
    from core.standard_api_types import StandardApiNamespace

    namespace = StandardApiNamespace(
        publication_id=str(publication["publication_id"]),
        api_generation=int(publication["api_generation"]),
        key_id=str(key["key_id"]),
        dialect="responses",
        api_model_id=str(publication["api_model_id"]),
    )
    deleted = A2AStore.instance().delete_api_response(
        namespace, response_id)
    if deleted is None:
        _openai_error(
            req,
            404,
            message=_NOT_FOUND_MESSAGE,
            error_type="invalid_request_error",
            code="not_found",
            request_id=request_id,
        )
        return
    _json_response(req, 200, {
        "id": response_id,
        "object": "response.deleted",
        "deleted": True,
    }, {"x-request-id": request_id})


def handle_anthropic_models(req) -> None:
    access = _anthropic_access(req)
    if access is None:
        return
    publication, _key = access
    request_id = _request_id()
    if not _anthropic_version_headers(req, request_id):
        return
    model_id = str(publication["api_model_id"])
    created = datetime.fromtimestamp(
        float(publication.get("created_at") or 0),
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    model = {
        "id": model_id,
        "created_at": created,
        "display_name": "PawFlow published agent",
        "type": "model",
    }
    _json_response(req, 200, {
        "data": [model],
        "has_more": False,
        "first_id": model_id,
        "last_id": model_id,
    }, {"request-id": request_id})


def handle_anthropic_messages(req) -> None:
    access = _anthropic_access(req)
    if access is None:
        return
    publication, key = access
    request_id = _request_id()
    if not _anthropic_version_headers(req, request_id):
        return
    body = _anthropic_request_object(req, request_id)
    if body is None:
        return
    from core.a2a_store import A2AStore
    from core.standard_api_anthropic import (
        AnthropicMessagesError,
        AnthropicMessagesRunError,
        iter_anthropic_message_sse,
        prepare_anthropic_message_run,
        wait_anthropic_message_payload,
    )
    try:
        admission = prepare_anthropic_message_run(
            A2AStore.instance(),
            publication,
            key,
            body,
            request_id=request_id,
        )
    except AnthropicMessagesError as exc:
        _anthropic_error(
            req,
            exc.status,
            message=str(exc),
            error_type=exc.error_type,
            request_id=request_id,
        )
        return
    except Exception:
        logger.exception("Anthropic Messages admission failed")
        _anthropic_error(
            req,
            500,
            message="The published agent request failed.",
            error_type="api_error",
            request_id=request_id,
        )
        return

    if admission.run.turn.stream:
        headers = dict(_ANTHROPIC_SSE_HEADERS)
        headers["request-id"] = request_id
        req.complete_stream(
            200,
            headers,
            iter_anthropic_message_sse(admission),
        )
        return

    def _complete_non_stream() -> None:
        try:
            payload = wait_anthropic_message_payload(admission.run)
        except AnthropicMessagesRunError as exc:
            _anthropic_error(
                req,
                exc.status,
                message=str(exc),
                error_type="api_error",
                request_id=request_id,
            )
        except Exception:
            logger.exception("Anthropic Messages response failed")
            _anthropic_error(
                req,
                500,
                message="The published agent request failed.",
                error_type="api_error",
                request_id=request_id,
            )
        else:
            _json_response(
                req,
                200,
                payload,
                {"request-id": request_id},
            )

    threading.Thread(
        target=_complete_non_stream,
        daemon=True,
        name=f"anthropic-messages-response-{request_id[:16]}",
    ).start()


def register_standard_api_routes(listener) -> None:
    """Register the final standard API route shapes idempotently."""

    existing = {
        (row.get("method", ""), row.get("pattern", ""))
        for row in listener.get_routes()
    }
    routes = [
        ("GET", "/openai/{publication_id}/v1/models", handle_openai_models),
        ("GET", "/openai/{publication_id}/v1/models/{model_id}",
         handle_openai_model),
        ("POST", "/openai/{publication_id}/v1/chat/completions",
         handle_openai_chat_completions),
        ("POST", "/openai/{publication_id}/v1/responses",
         handle_openai_responses),
        ("GET", "/openai/{publication_id}/v1/responses/{response_id}",
         handle_openai_response_get),
        ("DELETE", "/openai/{publication_id}/v1/responses/{response_id}",
         handle_openai_response_delete),
        ("GET", "/anthropic/{publication_id}/v1/models",
         handle_anthropic_models),
        ("POST", "/anthropic/{publication_id}/v1/messages",
         handle_anthropic_messages),
    ]
    for method, pattern, callback in routes:
        if (method, pattern) not in existing:
            listener.register_route(
                method,
                pattern,
                _ROUTE_OWNER,
                callback=callback,
                public=True,
            )


def ensure_standard_api_routes() -> None:
    from services.http_listener_service import HTTPListenerService

    listener = next(iter(HTTPListenerService.all_instances().values()), None)
    if listener is None:
        raise RuntimeError(
            "No HTTP listener is available for the standard agent API endpoint")
    register_standard_api_routes(listener)


__all__ = [
    "ensure_standard_api_routes",
    "handle_anthropic_messages",
    "handle_anthropic_models",
    "handle_openai_chat_completions",
    "handle_openai_response_delete",
    "handle_openai_response_get",
    "handle_openai_responses",
    "register_standard_api_routes",
]
