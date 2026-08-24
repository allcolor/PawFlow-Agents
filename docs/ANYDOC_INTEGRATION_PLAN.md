# Anydoc Document Conversion Integration Plan

## Status

Proposed. This plan was prepared on 2026-08-23 against PawFlow
`1.0.0-beta.242` and `firecrawl-anydoc` 0.2.3.

## Decision

Integrate the local Python bindings from
[firecrawl/anydoc](https://github.com/firecrawl/anydoc) as PawFlow's primary
converter for the document formats Anydoc supports.

The integration must be an internal conversion layer shared by agent
attachments and Graphify. It must not expose the Anydoc CLI as a raw agent tool,
install the upstream Agent Skill, or send documents to Firecrawl Parse.
PawFlow continues to own attachment storage, scope authorization, output
limits, OCR routing, error normalization, and prompt construction.

Keep MarkItDown only for formats outside Anydoc's scope and for the existing
explicitly configured vision/OCR fallback. Do not maintain two independent
Office conversion implementations after parity and rollout gates pass.

This work is unrelated to PawFlow's existing Firecrawl web-search provider in
`paperfoot/search-cli`. It adds no Firecrawl API key and does not change
`web_search`, web scraping, SaaS connectors, or MCP routing.

## Why Integrate It

PawFlow already converts many attachments, but the current behavior is split
across two implementations:

- `tasks/ai/agent_context.py` accepts a broad extension list, tries MarkItDown,
  then uses format-specific Python fallbacks.
- `core/graphify/detect.py` separately converts only PDF, DOCX, and XLSX.
- The two paths produce different Markdown and support different subsets of
  formats.
- Some accepted legacy or macro-enabled extensions have no dependable fallback
  when MarkItDown cannot parse them.
- Conversion failures are generally reduced to an empty string, which makes
  unsupported, encrypted, malformed, and resource-limited inputs
  indistinguishable.

Anydoc provides one content-detecting parser and one GitHub-Flavored Markdown
serializer for Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, CSV, and PDF.
Its Python binding releases the GIL and has typed, structured failure classes.
This gives PawFlow a practical way to broaden format coverage while deleting
duplicated parser code.

## Goals

- Produce consistent bounded GFM from supported document attachments.
- Support legacy and macro-enabled Office formats without LibreOffice.
- Detect formats from content where possible instead of trusting filenames.
- Preserve the original scoped `file_ref` and its FileStore lifecycle.
- Keep all conversion local by default.
- Retain opt-in PawFlow vision OCR for scanned or image-only documents.
- Reuse one conversion contract in agent context construction and Graphify.
- Return deterministic, sanitized failure categories without document content
  or secrets in logs.
- Preserve PawFlow's Python 3.10 through 3.13 support and release packaging
  matrix.
- Remove superseded conversion dependencies and duplicate code only after
  compatibility gates pass.

## Non-goals

- Do not call the hosted Firecrawl Parse API.
- Do not add a new Firecrawl credential or reuse `firecrawl_api_key`.
- Do not expose arbitrary document conversion as an unauthorised public HTTP or
  MCP endpoint.
- Do not automatically persist embedded assets in the first release.
- Do not execute macros, embedded programs, links, or OLE objects.
- Do not promise OCR from Anydoc itself; its local PDF path handles text-based
  PDFs.
- Do not change the FileStore authorization, TTL, or conversation ownership
  model.
- Do not merge this work with OpenConnector, CRW, or Search CLI integration.
- Do not retain a permanent legacy backend after the migration is proven.

## Current Baseline

### Agent attachments

`AgentContextMixin._build_user_content()` stores every non-image attachment as
a scoped `file_ref`, converts supported content, appends a separate
`[Extracted Markdown from ...]` text part, and truncates the extracted result
with `attachment_markdown_max_chars`, currently 30,000 characters by default.

`_extract_attachment_markdown()` currently:

1. decodes plain text, HTML, Markdown, CSV, JSON, and XML MIME types directly;
2. admits PDF, DOC/DOCX, XLS/XLSX, PPTX, ODT/ODS, RTF, EPUB, HTML, CSV, JSON,
   XML, TXT, Markdown, and ZIP by extension;
3. tries MarkItDown;
4. falls back to PyPDF2/pdfminer, python-docx, openpyxl, python-pptx, striprtf,
   or simple ZIP/XML/HTML extraction.

The MarkItDown path can use a PawFlow vision-capable `llmConnection` selected by
`attachment_ocr_llm_service` or
`PAWFLOW_MARKITDOWN_OCR_LLM_SERVICE`.

### Graphify

`core/graphify/detect.py` classifies only DOCX and XLSX as Office documents,
converts them into Markdown sidecars, and has a separate PDF text extractor.
Sensitive-path filtering happens before corpus conversion and must remain
authoritative.

### Packaging

PawFlow currently installs `python-docx`, `openpyxl`, `python-pptx`,
`striprtf`, `PyPDF2`, `ebooklib`, and
`markitdown[pdf,docx,xlsx,pptx]` for document handling.

As observed on 2026-08-23, `firecrawl-anydoc` 0.2.3:

- requires Python 3.10 or newer;
- publishes CPython 3.10 ABI3 wheels usable by newer CPython versions;
- publishes macOS x86_64 and arm64 wheels;
- publishes manylinux and musllinux x86_64 and arm64 wheels;
- publishes a Windows x86_64 wheel and a source distribution;
- is MIT licensed.

These observations are implementation inputs, not permanent assumptions.
The compatibility spike must revalidate the selected release and artifacts.

## Supported Format Target

| Family | First-release extensions | Primary route |
|---|---|---|
| Word | `.doc`, `.docx`, `.docm` | Anydoc |
| PowerPoint | `.ppt`, `.pps`, `.pot`, `.pptx`, `.pptm`, `.ppsx`, `.ppsm` | Anydoc |
| Excel | `.xls`, `.xlsx`, `.xlsm`, `.xlsb` | Anydoc |
| OpenDocument | `.odt`, `.ods`, `.odp` | Anydoc |
| Rich Text | `.rtf` | Anydoc |
| EPUB | `.epub` | Anydoc |
| CSV | `.csv` | Anydoc with an explicit format hint |
| PDF | `.pdf` | Anydoc, then configured OCR only for image-only input |
| Plain text | `.txt`, `.md`, `.json`, `.xml`, text MIME types | Existing bounded decoder |
| Web documents | `.html`, `.htm` | Existing HTML/MarkItDown route |
| Generic archives | `.zip` | No automatic document conversion |

Generic ZIP files must leave the automatic conversion allowlist. Office and
OpenDocument ZIP containers remain supported because Anydoc identifies their
package structure and enforces parser resource limits.

## Architecture

Add a small internal module, provisionally
`core/document_conversion.py`. It owns format admission, content detection,
backend routing, failure normalization, and output bounds. It must stay
independent from AgentContext, FileStore, Graphify, ServiceRegistry, and HTTP
transport code.

```text
Agent attachment or Graphify path
  -> existing scope/path/FileStore authorization
  -> PawFlow document conversion facade
       -> bounded plain-text decoder
       -> Anydoc for supported document bytes
       -> MarkItDown for non-Anydoc web/document formats
       -> configured PawFlow vision OCR for eligible image-only input
  -> normalized conversion result
  -> caller-specific output
       -> attachment: original file_ref + bounded Markdown context
       -> Graphify: deterministic Markdown sidecar
```

### Internal contract

Use a typed result rather than returning an ambiguous empty string:

```python
@dataclass(frozen=True)
class DocumentConversionResult:
    markdown: str
    detected_format: str
    backend: str
    truncated: bool = False
    warning_code: str = ""

def convert_document_bytes(
    raw: bytes,
    *,
    filename: str,
    mime_type: str,
    max_output_chars: int,
    ocr_fallback: Callable[..., str] | None = None,
) -> DocumentConversionResult | None:
    ...
```

The exact symbol names may change during implementation, but these invariants
must not:

- the converter receives bytes and metadata, not a FileStore identifier;
- callers authorize and load bytes before invoking it;
- the converter never stores files or resolves user/conversation scopes;
- a successful result contains non-empty bounded Markdown;
- the backend name is internal diagnostic metadata, not injected into prompts;
- public errors contain stable PawFlow codes, not upstream exception strings;
- raw document bytes and extracted text never enter INFO/WARNING logs.

Add a path wrapper only if Graphify can use it without bypassing its existing
sensitive-path checks. The bytes API remains authoritative so attachment and
path behavior cannot drift.

### Routing rules

1. Normalize MIME type and filename without trusting either as proof.
2. Enforce an input-byte limit before invoking any parser.
3. Decode known text MIME types with the existing bounded text path.
4. Ask Anydoc to detect supported binary formats from content.
5. Pass an explicit format only where content detection is impossible, notably
   CSV.
6. Reject a filename/content mismatch only when it represents an unsafe or
   unsupported payload; otherwise record the detected format internally.
7. Return Anydoc's GFM for supported documents.
8. Use MarkItDown only for admitted non-Anydoc formats or the existing
   configured OCR route.
9. If no route succeeds, preserve the `file_ref` and append the current
   non-convertible attachment marker.

Do not run the same malformed container through every installed parser.
Parser chaining expands attack surface and turns deterministic failures into
unbounded work.

### Error policy

Map upstream failures to stable internal categories:

| Anydoc failure | PawFlow behavior |
|---|---|
| `UnsupportedError` | Use OCR only for an eligible image-only PDF; otherwise preserve file only |
| `EncryptedError` | Stop; report `encrypted_document` without retrying another parser |
| `ResourceLimitError` | Stop; report `document_resource_limit` |
| `MalformedError` | Stop; report `malformed_document` |
| `MissingPartError` | Stop; report `incomplete_document` |
| `ValueError` for format hint | Treat as a PawFlow routing bug and cover with a regression test |
| Import/module unavailable | Use the temporary legacy route during rollout; expose a health diagnostic |
| Unexpected exception | Log class and safe code at DEBUG, never bytes/text/path secrets; preserve file only |

The ordinary LLM prompt should not receive stack traces or parser internals.
User-visible clients may receive a short sanitized conversion status alongside
the intact attachment reference.

## Execution and Concurrency

The two production call sites currently invoke `_build_user_content()`
synchronously from agent context preparation and pending-message drain.
The compatibility spike must confirm that both run on an agent worker and never
on the shared HTTP listener thread or event-loop thread.

Anydoc releases the GIL, but parsing still consumes CPU and memory. Therefore:

- never perform conversion in the HTTP ingress handler;
- keep parsing inside the existing agent execution boundary, or use a bounded
  PawFlow-owned executor if call-site tracing shows shared-loop execution;
- do not create an unbounded thread per attachment;
- bound concurrent conversions per agent turn and per process;
- preserve cancellation/force-stop semantics;
- measure one large document and several concurrent attachments;
- do not add global locks that serialize unrelated agents.

A timeout cannot safely kill arbitrary in-process native code. Input and parser
resource limits are the primary protection. If adversarial-fixture testing
shows native calls can exceed the allowed wall time, move conversion to a
supervised subprocess in a later gated phase rather than pretending a thread
timeout cancels it.

## Security and Privacy

Documents are untrusted input.

- Conversion is local and performs no provider network request.
- Add a test that blocks outbound sockets during ordinary Anydoc conversion.
- Never route to Firecrawl Parse automatically.
- Keep the original FileStore scope, owner, conversation, category, and TTL.
- Preserve Graphify's sensitive-path exclusion before reading or converting a
  file.
- Bound input bytes, output characters, package nesting, decompressed data,
  table dimensions, and aggregate attachments per turn.
- Treat `EncryptedError` and resource-limit failures as terminal.
- Do not execute macros or embedded objects.
- Do not dereference external links or remote images from a document.
- Do not persist embedded assets in phase 1.
- Keep extracted Markdown inside the same untrusted attachment envelope used
  today; it is data, not agent/system instruction.
- Escape the filename in the prompt label so it cannot forge a new message or
  delimiter.
- Redact credentials, filesystem roots, user identifiers, and document content
  from diagnostics.
- Include the dependency in license inventory, SBOM, vulnerability scans, and
  `THIRD_PARTY_NOTICES.md`.

Review Anydoc's committed resource limits against PawFlow's limits. PawFlow
must apply its own smaller limit where upstream limits are absent or too large.

## Configuration and Rollout Control

Keep the existing settings:

- `attachment_markdown_max_chars`;
- `attachment_ocr_llm_service`;
- `PAWFLOW_MARKITDOWN_OCR_LLM_SERVICE`.

During canary rollout, add one narrowly scoped selector:

`attachment_document_converter = auto | anydoc | legacy`

- `auto`: use Anydoc for its supported formats and controlled fallbacks
  elsewhere;
- `anydoc`: require Anydoc for its supported formats, useful for tests and
  diagnostics;
- `legacy`: temporary rollback during one migration window.

The final default is `auto`. Delete `legacy` and the selector once release
telemetry and fixtures prove the migration. PawFlow's zero-backward-
compatibility rule means the temporary backend is not a permanent public
contract.

Do not add endpoint, API-key, hosted/cloud, or provider-selection settings.

## Packaging Strategy

Preferred initial constraint:

```toml
"firecrawl-anydoc>=0.2.3,<0.3"
```

Anydoc is pre-1.0, so do not use an unbounded lower-only dependency. Revisit the
minor range deliberately after upstream compatibility tests.

Before adding the dependency to the default install:

1. install PawFlow from its built wheel in clean Python 3.10, 3.11, 3.12, and
   3.13 environments;
2. convert at least one fixture through the installed package, not the source
   checkout;
3. verify manylinux x86_64 and arm64;
4. verify musllinux x86_64 and arm64 if PawFlow documents Alpine support;
5. verify macOS x86_64 and arm64 and Windows x86_64 where release artifacts are
   supported;
6. confirm that no supported target silently requires a Rust toolchain;
7. record wheel sizes and final PawFlow image/install-size change;
8. verify license text and provenance.

Keep a guarded import and legacy fallback for the canary release. If a
supported target lacks a wheel, do not silently make the default PawFlow
installation compile Rust from an unpinned source toolchain. Either fix the
release matrix or keep Anydoc optional on that target with an explicit health
status.

## Dependency Cleanup

Do not remove existing conversion libraries in the same commit that first
introduces Anydoc.

After the parity window, search the full repository and remove a dependency
only if it has no remaining caller:

- `python-docx`;
- `openpyxl`;
- `python-pptx`;
- `striprtf`;
- `PyPDF2`;
- `ebooklib`;
- format extras from `markitdown` no longer needed outside OCR or non-Anydoc
  formats.

`PyPDF2` or other packages may still serve web-fetch or unrelated paths.
Dependency removal requires repository-wide evidence and its own clean build
and wheel smoke test.

## Delivery Phases

### Phase 0: Compatibility and supply-chain spike

- Pin one exact Anydoc release for evaluation.
- Build a fixture corpus across every target extension.
- Verify Python, OS, architecture, glibc, and musl artifacts.
- Confirm import time, wheel size, memory, and native-library behavior.
- Validate local-only operation with outbound network blocked.
- Review MIT licensing, dependency licenses, release provenance, and known
  vulnerabilities.
- Record differences from current MarkItDown and fallback output.
- Test encrypted, malformed, mislabeled, deeply nested, and oversized files.

Exit gate: every supported PawFlow release target has a documented install
route, no unexpected network call occurs, and no fixture can exceed agreed
resource budgets without a controlled failure.

### Phase 1: Shared conversion facade

- Add `core/document_conversion.py` and typed result/error categories.
- Add format admission and content/extension reconciliation.
- Add Anydoc conversion behind guarded import.
- Add bounded plain-text and controlled MarkItDown hooks.
- Add unit tests with no AgentContext or Graphify dependency.
- Add diagnostic health reporting that reveals backend availability and
  version without exposing document data.

Exit gate: the facade passes the complete format and hostile-input suite, has
one deterministic route per input, and produces only bounded results.

### Phase 2: Agent attachment integration

- Replace parser selection inside `_extract_attachment_markdown()` with the
  shared facade.
- Preserve `file_ref` creation before conversion.
- Preserve the extracted-Markdown content part and its output cap.
- Preserve opt-in vision/OCR service resolution.
- Add sanitized user-visible failure markers where useful.
- Add multi-attachment, queued-message, cancellation, and concurrency tests.
- Verify webchat, Telegram documents, PawCode, and pre-uploaded FileStore
  attachments.

Exit gate: every ingress path preserves the original reference and receives
equivalent or better bounded context without blocking HTTP ingress.

### Phase 3: Graphify integration

- Expand Graphify document classification to the approved Anydoc formats.
- Replace DOCX/XLSX/PDF-specific conversion with the shared facade.
- Keep sensitive/noise path filtering before byte reads.
- Keep deterministic sidecar naming and source comments.
- Make word counting consume the same converted representation.
- Add `tests/test_graphify_detect.py` with classification, conversion,
  collision, sensitive-path, and incremental-manifest cases.

Exit gate: attachment and Graphify conversion agree for the same bytes, and
Graphify never reads an excluded file merely to detect its format.

### Phase 4: Rollout and cleanup

- Enable `auto` in a canary release.
- Track only aggregate backend/failure/latency counters; never filenames,
  content, or raw exception strings.
- Compare conversion success and latency against the legacy baseline.
- Delete format-specific duplicate converters after the observation window.
- Remove proven-unused dependencies.
- Delete the temporary `legacy` selector.
- Update release notes and public documentation.

Exit gate: no supported-platform regression, no scope/privacy regression,
stable resource use under concurrency, and no remaining duplicate parser path
for Anydoc-supported formats.

## Required Tests

### Format and quality

- One valid fixture for every listed extension.
- DOC/DOCM, PPT/PPTM, XLS/XLSM/XLSB, and ODP regressions that current PawFlow
  does not reliably cover.
- Headings, nested lists, tables, merged cells, links, footnotes, speaker
  notes, equations, Unicode, and right-to-left text where the source format
  supports them.
- CSV with an explicit hint and a mislabeled non-CSV file.
- Content detection when a supported file has the wrong extension.
- Empty but valid, password-protected, malformed, truncated, and unsupported
  files.
- Text PDF and image-only PDF behavior.
- Stable structural assertions rather than full snapshots of all upstream
  whitespace.

### PawFlow behavior

- Original `file_ref` survives success, failure, truncation, and OCR fallback.
- FileStore ownership and conversation isolation remain enforced.
- `attachment_markdown_max_chars` is enforced after every backend.
- Filenames cannot escape the attachment prompt delimiter.
- Multiple attachments preserve order and independent status.
- Queued messages use the same conversion path as immediate messages.
- OCR is never called without explicit configuration and an eligible failure.
- No conversion logs contain document bytes, extracted text, or credentials.
- No ordinary conversion opens a network connection.
- Force stop and cancellation leave no leaked process, thread, file, or
  temporary artifact.

### Resource and hostile-input tests

- Maximum accepted input size and one-byte-over rejection.
- Deep ZIP/package nesting.
- High compression ratio.
- Huge table dimensions and repeated shared strings.
- Excessive embedded assets.
- Path traversal names inside document containers.
- External relationships and remote images are not fetched.
- Concurrent conversion at the configured process limit.
- Repeated failures do not grow memory or retain native objects.

### Packaging and CI

- Full PawFlow test suite.
- Compile-all, Ruff error classes, Bandit, and build.
- Clean-wheel install and import.
- Clean-wheel conversion smoke on Python 3.10 through 3.13.
- Platform/architecture wheel matrix described in Packaging Strategy.
- Docker server-image build and runtime smoke.
- License/notice and vulnerability scans.

## Benchmark Plan

Use a committed redistributable fixture set plus generated stress documents.
Measure:

- import latency;
- median and p95 conversion latency per format;
- peak RSS per conversion;
- output size and truncation rate;
- four concurrent small documents;
- one maximum-size document beside ordinary agent work;
- Anydoc versus current MarkItDown/fallback success rate;
- server image and installed-wheel size delta.

Upstream benchmark claims are context, not PawFlow acceptance evidence.
PawFlow gates use its own fixtures, limits, hardware class, and concurrency
model.

## File-level Implementation Map

Expected files, subject to the compatibility spike:

- `pyproject.toml`: bounded Anydoc dependency and later dependency cleanup.
- `core/document_conversion.py`: shared internal facade.
- `tasks/ai/agent_context.py`: delegate document routing; retain FileStore and
  OCR ownership.
- `core/graphify/detect.py`: shared conversion and expanded classification.
- `tests/test_document_conversion.py`: format, error, security, and resource
  tests.
- `tests/test_image_resize.py` or a new focused attachment test module:
  `file_ref` plus Markdown contract.
- `tests/test_graphify_detect.py`: Graphify classification and sidecars.
- `docs/tasks.md`: attachment behavior, supported formats, limits, and OCR.
- Graphify documentation: supported corpus documents and failure behavior.
- `THIRD_PARTY_NOTICES.md`: Anydoc and native binding license/provenance.
- `CHANGELOG.md` and `PROJECT_SUMMARY.md`: release-facing capability summary.

If `core/document_conversion.py` approaches the repository's 800-line limit,
split format routing, error normalization, and optional OCR adapters into a
small package before merging; do not let AgentContext grow again.

## Documentation Requirements

The implementation release must document:

- the exact supported extension matrix;
- content-based detection and the CSV exception;
- local conversion versus hosted Firecrawl Parse;
- the fact that no Firecrawl API key is used;
- scanned-PDF OCR requirements and cost/privacy implications of the selected
  PawFlow vision provider;
- input/output limits and sanitized failure states;
- embedded-asset behavior;
- platform availability and health diagnostics;
- the unchanged original `file_ref` lifecycle.

Use “Anydoc” for the project and `firecrawl-anydoc` for the Python package.
Do not imply that Firecrawl sponsors, endorses, or operates PawFlow.

## Acceptance Criteria

The integration is complete only when:

- all target formats convert through one shared PawFlow facade;
- attachment and Graphify output agree for identical input bytes;
- original attachments remain available as scoped `file_ref` objects;
- no ordinary conversion sends data over the network;
- image-only PDFs use OCR only when explicitly configured;
- encrypted, malformed, incomplete, and resource-limited inputs fail safely;
- input, output, concurrency, and memory budgets are enforced;
- Python 3.10 through 3.13 clean-wheel smoke tests pass;
- supported OS/architecture installs do not unexpectedly require Rust;
- full tests, compile-all, Ruff, Bandit, build, and Docker smoke pass;
- third-party notices and user documentation are complete;
- the temporary legacy route and unused duplicate dependencies are removed;
- rollback before that deletion is a configuration change, not a code revert.

## Open Questions for Phase 0

Resolve these with fixtures and packaging evidence, not assumptions:

1. What attachment input-byte limit should PawFlow enforce per file and per
   turn?
2. Does Anydoc's resource-limit policy stay below PawFlow's acceptable memory
   ceiling for every supported format?
3. Which current MarkItDown features remain necessary outside OCR and HTML?
4. Does PawFlow officially support a platform for which Anydoc 0.2.3 has no
   binary wheel?
5. Are embedded asset bytes needed soon enough to justify a separately scoped
   FileStore design, or should they remain discarded after alt-text rendering?
6. Do current agent worker call sites already guarantee that conversion never
   runs on an HTTP/event-loop thread?
7. Which aggregate counters are useful enough to retain after the canary
   backend selector is deleted?
