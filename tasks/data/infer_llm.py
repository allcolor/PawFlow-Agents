"""InferLLM Task - AI inference on FlowFile content.

Sends FlowFile content to an LLM provider and returns the response.
Supports system prompts, prompt templates with attribute interpolation,
JSON mode, and configurable model parameters.
"""

import logging
import time
from typing import Dict, Any, List

from core.base_task import BaseTask
from core import FlowFile, TaskFactory

logger = logging.getLogger(__name__)


class InferLLMTask(BaseTask):
    """Send FlowFile content to an LLM for inference.

    The FlowFile content is used as the user message.
    The LLM response becomes the new FlowFile content.

    Config:
        provider: "openai" or "anthropic" (default: openai)
        api_key: API key (required)
        base_url: API base URL (optional, for self-hosted)
        model: Model name (optional, uses provider default)
        system_prompt: System prompt (supports ${attr.name} interpolation)
        temperature: Sampling temperature (default: 0.7)
        max_tokens: Max response tokens (default: 1024)
        response_format: "text" or "json" (default: text)
        input_attribute: If set, use this attribute instead of content as input
        output_attribute: If set, store response in this attribute instead of content
        keep_original: If true, keep original content and add response as attribute (default: false)
        timeout: Request timeout in seconds (default: 60)

    Output attributes:
        llm.model: Model used
        llm.tokens_in: Input tokens
        llm.tokens_out: Output tokens
        llm.duration_ms: Request duration
        llm.finish_reason: Stop reason
    """

    TYPE = "inferLLM"
    VERSION = "1.0.0"
    NAME = "Infer LLM"
    DESCRIPTION = "Send content to an LLM and get the response"
    ICON = "ai"

    def get_parameter_schema(self) -> Dict[str, Any]:
        # api_key is required unless a service reference is provided
        api_key_required = "service" not in self.config
        return {
            "service": {
                "type": "string", "required": False, "default": "",
                "description": "Reference to an llmConnection service (overrides provider/api_key/base_url/model)",
            },
            "provider": {
                "type": "string", "required": False, "default": "openai",
                "description": "LLM provider: openai, anthropic",
            },
            "api_key": {
                "type": "string", "required": api_key_required, "sensitive": True,
                "description": "API key for the LLM provider",
            },
            "base_url": {
                "type": "string", "required": False, "default": "",
                "description": "API base URL (for self-hosted or compatible APIs)",
            },
            "model": {
                "type": "string", "required": False, "default": "",
                "description": "Model name (empty = provider default)",
            },
            "system_prompt": {
                "type": "string", "required": False, "default": "",
                "description": "System prompt (supports ${attr} interpolation)",
            },
            "temperature": {
                "type": "float", "required": False, "default": 0.7,
                "description": "Sampling temperature (0-2)",
            },
            "max_tokens": {
                "type": "integer", "required": False, "default": 65536,
                "description": "Maximum response tokens (default 64k for thinking models)",
            },
            "response_format": {
                "type": "string", "required": False, "default": "text",
                "description": "Response format: text or json",
            },
            "input_attribute": {
                "type": "string", "required": False, "default": "",
                "description": "Use this attribute as input instead of content",
            },
            "output_attribute": {
                "type": "string", "required": False, "default": "",
                "description": "Store response in attribute instead of content",
            },
            "keep_original": {
                "type": "boolean", "required": False, "default": False,
                "description": "Keep original content (store response as attribute)",
            },
            "timeout": {
                "type": "integer", "required": False, "default": 60,
                "description": "Request timeout in seconds",
            },
        }

    def execute(self, flowfile: FlowFile) -> List[FlowFile]:
        from services.llm_connection import LLMConnectionService, LLMMessage

        # Build service config
        provider = self.config.get("provider", "openai")
        api_key = self.config.get("api_key", "")
        base_url = self.config.get("base_url", "")
        model = self.config.get("model", "")
        timeout = int(self.config.get("timeout", 60))

        svc_config = {
            "provider": provider,
            "api_key": api_key,
            "timeout": timeout,
        }
        if base_url:
            svc_config["base_url"] = base_url
        if model:
            svc_config["default_model"] = model

        # Create service (per-execution, lightweight)
        svc = LLMConnectionService(svc_config)
        svc.connect()

        try:
            # Get input text
            input_attr = self.config.get("input_attribute", "")
            if input_attr:
                user_text = flowfile.get_attribute(input_attr) or ""
            else:
                user_text = flowfile.get_content().decode("utf-8", errors="replace")

            # Build messages
            messages = []
            system_prompt = self.config.get("system_prompt", "")
            # Synthetic conv-scope for infer pipelines: inferLLM is a
            # data-flow processor, the "conversation" is a one-shot
            # prompt scoped to this flow's service_id.
            _infer_cid = f"_infer:{self.config.get('_service_id', 'infer')}"
            if system_prompt:
                # Interpolate ${attr.name} from FlowFile attributes
                resolved = self._interpolate(system_prompt, flowfile)
                messages.append(LLMMessage(role="system", content=resolved,
                                            conversation_id=_infer_cid))

            messages.append(LLMMessage(role="user", content=user_text,
                                        conversation_id=_infer_cid))

            logger.debug(
                "inferLLM input: %d chars, fragment.id=%s",
                len(user_text), flowfile.get_attribute("fragment.identifier"),
            )

            # Call LLM
            temperature = float(self.config.get("temperature", 0.7))
            response_format = self.config.get("response_format", "text")
            fmt = "json" if response_format == "json" else None

            max_tokens = int(self.config.get("max_tokens", 65536))
            kwargs = dict(
                messages=messages,
                model=model or None,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=fmt,
            )

            response = svc.complete(**kwargs)

            logger.debug(
                "inferLLM response: %d chars, model=%s, tokens=%d/%d, finish=%s",
                len(response.content), response.model,
                response.tokens_in, response.tokens_out, response.finish_reason,
            )
            if not response.content:
                logger.warning("inferLLM: empty response (tokens_out=%d, finish=%s) — "
                               "thinking model may need higher max_tokens",
                               response.tokens_out, response.finish_reason)

            # Set output
            output_attr = self.config.get("output_attribute", "")
            keep_original = self.config.get("keep_original", False)

            if output_attr:
                flowfile.set_attribute(output_attr, response.content)
            elif keep_original:
                flowfile.set_attribute("llm.response", response.content)
            else:
                flowfile.set_content(response.content.encode("utf-8"))

            # Set metadata attributes
            flowfile.set_attribute("llm.model", response.model)
            flowfile.set_attribute("llm.tokens_in", str(response.tokens_in))
            flowfile.set_attribute("llm.tokens_out", str(response.tokens_out))
            flowfile.set_attribute("llm.duration_ms", f"{response.duration_ms:.1f}")
            flowfile.set_attribute("llm.finish_reason", response.finish_reason)

            return [flowfile]

        finally:
            svc.disconnect()

    def _interpolate(self, template: str, flowfile: FlowFile) -> str:
        """Simple ${attr.name} interpolation from FlowFile attributes."""
        result = template
        for key, value in flowfile.get_attributes().items():
            result = result.replace(f"${{{key}}}", value)
        return result


TaskFactory.register(InferLLMTask)
