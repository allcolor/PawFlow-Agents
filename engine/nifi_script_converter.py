"""Groovy → Python script converter for NiFi imports.

Built-in PawFlow feature (not a task/flow). Uses shared LLM client from core/llm_client.py.
Config stored in pawflow settings (api_key, base_url, model).

Supports:
- LLM-based conversion with specialized prompt
- Static regex fallback when no LLM configured
- Interactive mode: convert, review, re-submit with feedback
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from core.llm_client import LLMClient, LLMMessage, LLMResponse, LLMClientError

logger = logging.getLogger(__name__)


# ============================================================================
# NiFi Groovy API → PawFlow Python API mapping (used in LLM prompt + static fallback)
# ============================================================================

API_MAPPING_TABLE = """
## NiFi Groovy API → PawFlow Python API

| NiFi Groovy | PawFlow Python | Notes |
|---|---|---|
| `session.get()` | `flowfile` (parameter of execute()) | FlowFile is passed as argument |
| `session.read(flowfile, ...)` | `flowfile.get_content()` | Returns bytes |
| `session.write(flowfile, ...)` | `flowfile.set_content(data)` | Accepts bytes |
| `session.transfer(flowfile, REL_SUCCESS)` | `return [flowfile]` | Return list of FlowFiles |
| `session.transfer(flowfile, REL_FAILURE)` | `raise TaskError("...")` | Or set route.relationship |
| `flowFile.getAttribute(key)` | `flowfile.get_attribute(key)` | |
| `flowFile.putAttribute(key, val)` | `flowfile.set_attribute(key, val)` | |
| `flowFile.removeAttribute(key)` | `flowfile.remove_attribute(key)` | |
| `flowFile.getAttributes()` | `flowfile.get_attributes()` | Returns dict |
| `session.putAttribute(ff, k, v)` | `flowfile.set_attribute(k, v)` | In-place |
| `new FlowFile()` | `FlowFile(content=b"", attributes={})` | Import from core |
| `String`, `Integer`, `Long` | `str`, `int`, `int` | Java → Python types |
| `ArrayList`, `HashMap` | `list`, `dict` | |
| `JsonSlurper().parseText(s)` | `json.loads(s)` | `import json` |
| `JsonOutput.toJson(obj)` | `json.dumps(obj)` | `import json` |
| `new File(path).text` | `Path(path).read_text()` | `from pathlib import Path` |
| `import groovy.json.*` | `import json` | |
| `import java.util.*` | (remove) | Not needed in Python |
| `import java.io.*` | `import io` or `from pathlib import Path` | |
| `log.info(msg)` | `logger.info(msg)` | `import logging; logger = logging.getLogger(__name__)` |
| `log.warn(msg)` | `logger.warning(msg)` | |
| `log.error(msg)` | `logger.error(msg)` | |
| `try { ... } catch (Exception e) { ... }` | `try: ... except Exception as e: ...` | |
| `null` | `None` | |
| `true` / `false` | `True` / `False` | |
"""

SYSTEM_PROMPT = f"""You are a NiFi Groovy-to-Python converter for the PawFlow framework.

Your job: convert a NiFi ExecuteScript/ExecuteGroovyScript Groovy script into a PawFlow-compatible Python script.

## PawFlow executeScript task format

The converted Python script will be passed as the `script` parameter of a PawFlow `executeScript` task.
The script receives a `flowfile` variable (a FlowFile object) and must return a list of FlowFiles.

```python
# PawFlow executeScript template
import json
import logging
from core import FlowFile, TaskError

logger = logging.getLogger(__name__)

# flowfile is available as a local variable
content = flowfile.get_content()  # bytes
text = content.decode('utf-8')

# ... your conversion logic ...

flowfile.set_content(result.encode('utf-8'))
flowfile.set_attribute('my_attr', 'my_value')

# Return list of FlowFiles
result_flowfiles = [flowfile]
```

{API_MAPPING_TABLE}

## Rules
1. Convert ALL Java/Groovy types to Python equivalents
2. Replace NiFi session API calls with PawFlow FlowFile API
3. Remove all Java imports, add Python imports as needed
4. Convert `session.transfer(ff, REL_SUCCESS)` to adding ff to the return list
5. Convert `session.transfer(ff, REL_FAILURE)` to `raise TaskError("reason")`
6. Mark uncertain conversions with `# TODO: manual review - <reason>`
7. Handle encoding: NiFi uses Java strings (UTF-16), PawFlow uses bytes
8. Keep the logic structure, don't over-refactor

## Output format
Return ONLY the Python code, no explanations. Start with imports, end with `result_flowfiles = [...]`.
If there are warnings, put them as # TODO comments in the code.
"""

FEEDBACK_PROMPT = """The user has reviewed your conversion and provides feedback:

{feedback}

Previous converted script:
```python
{previous_script}
```

Original Groovy script:
```groovy
{original_groovy}
```

Please fix the conversion based on the feedback. Return ONLY the corrected Python code.
"""


@dataclass
class ScriptConversionResult:
    """Result of a single script conversion."""
    original_groovy: str
    converted_python: str
    warnings: List[str] = field(default_factory=list)
    used_llm: bool = False
    llm_tokens_used: int = 0
    success: bool = True
    error: str = ""


class NiFiScriptConverter:
    """Converts NiFi Groovy scripts to PawFlow Python scripts.

    Uses LLM for intelligent conversion, with static regex fallback.
    Supports interactive re-submission with user feedback.
    """

    def __init__(self, llm_config: Optional[Dict[str, Any]] = None):
        """Initialize with optional LLM config.

        Args:
            llm_config: Dict with provider, api_key, base_url, default_model, etc.
                        If None, uses static conversion only.
        """
        self._llm_client: Optional[LLMClient] = None
        if llm_config and llm_config.get("api_key"):
            self._llm_client = LLMClient.from_config(llm_config)

    @property
    def has_llm(self) -> bool:
        return self._llm_client is not None

    def convert(self, groovy_script: str, max_tokens: int = 4096) -> ScriptConversionResult:
        """Convert a Groovy script to Python.

        Uses LLM if available, falls back to static regex conversion.
        """
        if not groovy_script or not groovy_script.strip():
            return ScriptConversionResult(
                original_groovy=groovy_script,
                converted_python="# Empty script\nresult_flowfiles = [flowfile]",
                warnings=["Original script was empty"],
            )

        if self._llm_client:
            return self._convert_with_llm(groovy_script, max_tokens)
        else:
            return self._convert_static(groovy_script)

    def convert_with_feedback(
        self,
        original_groovy: str,
        previous_python: str,
        feedback: str,
        max_tokens: int = 4096,
    ) -> ScriptConversionResult:
        """Re-submit a conversion with user feedback for LLM correction.

        Args:
            original_groovy: Original Groovy script
            previous_python: Previously converted Python script
            feedback: User's feedback/corrections to apply
            max_tokens: Max tokens for LLM response
        """
        if not self._llm_client:
            return ScriptConversionResult(
                original_groovy=original_groovy,
                converted_python=previous_python,
                success=False,
                error="LLM not configured — manual editing required",
            )

        prompt = FEEDBACK_PROMPT.format(
            feedback=feedback,
            previous_script=previous_python,
            original_groovy=original_groovy,
        )

        try:
            messages = [
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(role="user", content=prompt),
            ]
            response = self._llm_client.complete(
                messages, temperature=0.2, max_tokens=max_tokens,
            )
            python_code = self._extract_code(response.content)
            warnings = self._extract_todos(python_code)

            return ScriptConversionResult(
                original_groovy=original_groovy,
                converted_python=python_code,
                warnings=warnings,
                used_llm=True,
                llm_tokens_used=response.total_tokens,
            )
        except LLMClientError as e:
            return ScriptConversionResult(
                original_groovy=original_groovy,
                converted_python=previous_python,
                success=False,
                error=f"LLM error: {e}",
            )

    # ========================================================================
    # LLM conversion
    # ========================================================================

    def _convert_with_llm(self, groovy_script: str, max_tokens: int) -> ScriptConversionResult:
        """Convert using LLM."""
        try:
            messages = [
                LLMMessage(role="system", content=SYSTEM_PROMPT),
                LLMMessage(
                    role="user",
                    content=f"Convert this NiFi Groovy script to PawFlow Python:\n\n```groovy\n{groovy_script}\n```",
                ),
            ]
            response = self._llm_client.complete(
                messages, temperature=0.2, max_tokens=max_tokens,
            )
            python_code = self._extract_code(response.content)
            warnings = self._extract_todos(python_code)

            return ScriptConversionResult(
                original_groovy=groovy_script,
                converted_python=python_code,
                warnings=warnings,
                used_llm=True,
                llm_tokens_used=response.total_tokens,
            )
        except LLMClientError as e:
            logger.warning(f"LLM conversion failed, falling back to static: {e}")
            result = self._convert_static(groovy_script)
            result.warnings.append(f"LLM conversion failed ({e}), used static fallback")
            return result

    # ========================================================================
    # Static regex conversion (fallback)
    # ========================================================================

    def _convert_static(self, groovy_script: str) -> ScriptConversionResult:
        """Best-effort static conversion using regex patterns."""
        warnings = []
        code = groovy_script

        # Remove Java/Groovy imports
        code = re.sub(r'^import\s+(?:groovy|java|org\.apache)\..*$', '', code, flags=re.MULTILINE)

        # Add Python imports
        imports = ["import json", "import logging", "from core import FlowFile, TaskError", "",
                   "logger = logging.getLogger(__name__)", ""]

        # session.get() → flowfile (already available)
        code = re.sub(r'(?:def\s+)?(?:FlowFile\s+)?(\w+)\s*=\s*session\.get\(\)', r'# \1 = flowfile (already available)', code)

        # session.read() patterns
        code = re.sub(
            r'session\.read\(\s*(\w+)\s*,\s*\{[^}]*inputStream\s*->\s*',
            r'_content = \1.get_content()  # ',
            code,
        )

        # session.write() patterns
        code = re.sub(
            r'session\.write\(\s*(\w+)\s*,\s*\{[^}]*outputStream\s*->\s*',
            r'\1.set_content(  # ',
            code,
        )

        # FlowFile attribute access
        code = re.sub(r'(\w+)\.getAttribute\(\s*["\']([^"\']+)["\']\s*\)', r'\1.get_attribute("\2")', code)
        code = re.sub(r'(\w+)\.putAttribute\(\s*["\']([^"\']+)["\']\s*,\s*([^)]+)\)', r'\1.set_attribute("\2", \3)', code)
        code = re.sub(r'session\.putAttribute\(\s*(\w+)\s*,\s*["\']([^"\']+)["\']\s*,\s*([^)]+)\)',
                       r'\1.set_attribute("\2", \3)', code)

        # session.transfer
        code = re.sub(r'session\.transfer\(\s*(\w+)\s*,\s*REL_SUCCESS\s*\)', r'# \1 transferred to success', code)
        code = re.sub(r'session\.transfer\(\s*(\w+)\s*,\s*REL_FAILURE\s*\)', r'raise TaskError("Failure")', code)

        # Java types
        code = code.replace('new ArrayList()', '[]')
        code = code.replace('new HashMap()', '{}')
        code = re.sub(r'new\s+JsonSlurper\(\)\.parseText\(([^)]+)\)', r'json.loads(\1)', code)
        code = re.sub(r'JsonOutput\.toJson\(([^)]+)\)', r'json.dumps(\1)', code)

        # Groovy syntax → Python
        code = code.replace('null', 'None')
        code = code.replace('true', 'True')
        code = code.replace('false', 'False')
        code = re.sub(r'log\.info\(', 'logger.info(', code)
        code = re.sub(r'log\.warn\(', 'logger.warning(', code)
        code = re.sub(r'log\.error\(', 'logger.error(', code)

        # Groovy closures → comments
        code = re.sub(r'\{[^{]*->\s*$', '# TODO: manual review - closure conversion', code, flags=re.MULTILINE)

        # def → (keep as is, Python also uses def)
        # Remove semicolons
        code = re.sub(r';\s*$', '', code, flags=re.MULTILINE)

        # Remove empty lines clusters
        code = re.sub(r'\n{3,}', '\n\n', code)

        # Build final script
        final_lines = imports + [code.strip(), "", "# TODO: manual review - static conversion (no LLM)",
                                  "result_flowfiles = [flowfile]"]
        final = "\n".join(final_lines)

        warnings.append("Static conversion (no LLM) — requires manual review")

        return ScriptConversionResult(
            original_groovy=groovy_script,
            converted_python=final,
            warnings=warnings,
            used_llm=False,
        )

    # ========================================================================
    # Utilities
    # ========================================================================

    def _extract_code(self, llm_output: str) -> str:
        """Extract Python code from LLM response (may be wrapped in ```python blocks)."""
        # Try to extract from code block
        match = re.search(r'```python\s*\n(.*?)```', llm_output, re.DOTALL)
        if match:
            return match.group(1).strip()
        match = re.search(r'```\s*\n(.*?)```', llm_output, re.DOTALL)
        if match:
            return match.group(1).strip()
        # No code block, return as-is
        return llm_output.strip()

    def _extract_todos(self, python_code: str) -> List[str]:
        """Extract TODO comments from converted code."""
        return re.findall(r'#\s*TODO:?\s*(.+)$', python_code, re.MULTILINE)
