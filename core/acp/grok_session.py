"""Correlate Grok's private completion notification with one pending ACP prompt."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any

from acp import RequestError
from acp.schema import PromptResponse

from core.acp.client_adapter import AcpClientAdapter, AcpClientHandlers
from core.acp.process_session import AcpProcessSession


class GrokAcpProcessSession(AcpProcessSession):
    """Race the standard prompt response against a matching native completion."""

    def __init__(
        self, *args: Any, handlers: AcpClientHandlers | None = None, **kwargs: Any
    ) -> None:
        original = handlers or AcpClientHandlers()
        self._native_notification = original.ext_notification
        self._pending_completions: dict[str, tuple[str, asyncio.Future[Any]]] = {}
        handlers = replace(
            original,
            ext_notification=self._completion_notification,
            extension_notifications=(
                *original.extension_notifications,
                "x.ai/session/prompt_complete",
            ),
        )
        super().__init__(*args, handlers=handlers, **kwargs)

    async def _completion_notification(self, method: str, params: dict[str, Any]) -> None:
        if method.removeprefix("_") != "x.ai/session/prompt_complete":
            if self._native_notification is not None:
                await AcpClientAdapter._invoke(self._native_notification, method, params)
            return
        if isinstance(params.get("params"), dict) and "method" in params:
            if str(params["method"]).removeprefix("_") != "x.ai/session/prompt_complete":
                return
            params = params["params"]
        # A session id alone cannot distinguish a late notification from this turn.
        # The outgoing prompt always supplies both correlation ids.
        prompt_id = params.get("promptId")
        if not isinstance(prompt_id, str):
            return
        pending = self._pending_completions.get(prompt_id)
        if pending is None or params.get("sessionId") != pending[0]:
            return
        future = pending[1]
        if future.done():
            return
        reason = params.get("stopReason")
        if reason == "rate_limit":
            future.set_exception(RequestError(-32003, "Grok usage limit reached"))
        elif reason not in {"end_turn", "cancelled", "max_tokens", "max_turn_requests", "refusal"}:
            future.set_exception(
                RequestError.internal_error(
                    {
                        "message": "Grok prompt failed or returned no valid stop reason",
                        "agentResult": params.get("agentResult"),
                    }
                )
            )
        else:
            future.set_result(
                PromptResponse(
                    stop_reason=reason,
                    field_meta={
                        "promptId": prompt_id,
                        "requestId": prompt_id,
                        "agentResult": params.get("agentResult"),
                    },
                )
            )

    async def _prompt_response(self, connection: Any, session_id: str, prompt: list[Any]) -> Any:
        prompt_id = str(uuid.uuid4())
        completion = asyncio.get_running_loop().create_future()
        self._pending_completions[prompt_id] = (session_id, completion)
        standard = asyncio.create_task(
            connection.prompt(
                session_id=session_id,
                prompt=prompt,
                promptId=prompt_id,
                requestId=prompt_id,
            )
        )
        try:
            done, _ = await asyncio.wait(
                (standard, completion),
                return_when=asyncio.FIRST_COMPLETED,
            )
            # Prefer the standard ACP response if both arrive together.
            return standard.result() if standard in done else completion.result()
        finally:
            self._pending_completions.pop(prompt_id, None)
            for future in (standard, completion):
                if not future.done():
                    future.cancel()
            await asyncio.gather(standard, completion, return_exceptions=True)

    def cancel(self, session_id: str, *, timeout: float | None = None) -> None:
        _, loop = self._running_connection()

        def cancel_pending() -> None:
            for owner, future in self._pending_completions.values():
                if owner == session_id and not future.done():
                    future.set_result(PromptResponse(stop_reason="cancelled"))

        loop.call_soon_threadsafe(cancel_pending)
        super().cancel(session_id, timeout=timeout)
