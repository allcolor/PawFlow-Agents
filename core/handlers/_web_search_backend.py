"""Server search-service selection and bounded free-search execution."""

import logging
from concurrent.futures import ThreadPoolExecutor, wait


logger = logging.getLogger(__name__)


class WebSearchBackendMixin:
    """Backend orchestration shared by the web search handler."""

    def _resolve_search_service(self, arguments):
        service_id = str(arguments.get("service") or "").strip()
        try:
            from core.service_registry import ServiceRegistry

            registry = ServiceRegistry.get_instance()
            if service_id:
                service = registry.resolve(
                    service_id,
                    user_id=self._user_id,
                    conv_id=self._conversation_id,
                )
                if service is None:
                    return None, f"service '{service_id}' was not found"
                if getattr(service, "TYPE", "") != "webSearchConnection":
                    return None, (
                        f"service '{service_id}' is not a webSearchConnection"
                    )
                return service, ""

            definitions = registry.resolve_by_type(
                "webSearchConnection",
                user_id=self._user_id,
                conv_id=self._conversation_id,
            )
            if not definitions:
                return None, ""

            def scope_rank(item):
                if item.scope == "conv":
                    return 0 if item.scope_id == self._conversation_id else 1
                if item.scope == "user":
                    return 2
                return 3

            selected = min(
                definitions,
                key=lambda item: (scope_rank(item), item.service_id),
            )
            return registry.get_live_instance(
                selected.scope,
                selected.scope_id,
                selected.service_id,
            ), ""
        except Exception as exc:
            logger.debug("web search service resolution failed: %s", exc)
            return None, str(exc)

    def _render_cli_response(self, query, payload):
        results = payload.get("results") or []
        normalized = []
        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            normalized.append({
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or ""),
                "provider": str(item.get("source") or "search-cli"),
            })

        answers = []
        for item in payload.get("answers") or []:
            text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if not text:
                continue
            provider = str(item.get("provider") or "search-cli")
            answers.append(f"Answer [{provider}]:\n{text}")

        if not normalized and not answers:
            return ""

        metadata = payload.get("metadata") or {}
        elapsed = metadata.get("elapsed_ms")
        suffix = (
            f", {elapsed} ms" if isinstance(elapsed, (int, float)) else ""
        )
        sections = answers + [
            self._format_result(item) for item in normalized
        ]
        return (
            f"Search results for '{query}' (search-cli{suffix}):\n\n"
            + "\n\n".join(sections)
        )

    def _search_free_concurrently(
        self,
        providers,
        query,
        max_results,
        deadline_seconds,
    ):
        attempts = []
        collected = []
        executor = ThreadPoolExecutor(
            max_workers=len(providers),
            thread_name_prefix="pawflow-web-search",
        )
        futures = {
            executor.submit(
                self._search_provider,
                provider,
                query,
                max_results,
            ): provider
            for provider in providers
        }
        done, pending = wait(futures, timeout=deadline_seconds)

        for future in done:
            provider = futures[future]
            try:
                results = future.result()
            except Exception as exc:
                attempts.append(f"{provider}: {exc}")
                continue
            for result in results or []:
                result.setdefault("provider", provider)
            collected.extend(results or [])
            attempts.append(
                f"{provider}: {len(results)} result(s)"
                if results
                else f"{provider}: no parseable results"
            )

        for future in pending:
            attempts.append(f"{futures[future]}: deadline exceeded")
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return attempts, collected
