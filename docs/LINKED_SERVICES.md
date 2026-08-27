# Linked Services

The Webchat resource sidebar exposes one **Linked services** section for
conversation-scoped overrides of PawFlow's automatic processing roles. Every
binding is optional. An upgrade does not create a binding, install a service,
or change an existing automatic route.

## Resolution contract

For each role, PawFlow resolves targets in this order:

1. the existing explicit agent, flow, environment, or parameter configuration;
2. the conversation's linked-service override;
3. the historical PawFlow behavior for that component.

Selecting **Return to PawFlow default** deletes the role binding. PawFlow does
not persist a synthetic `auto` target. If an explicitly linked target becomes
disabled, deleted, or incompatible, the UI marks it unavailable and the owner
component resumes its historical fallback instead of selecting another target
silently.

Bindings reference only services already visible to the conversation in
conversation, user, or global scope. Installing a service or adding an agent to
a conversation never links it automatically.

## Roles

| Role | Compatible explicit target | Behavior without an override |
|---|---|---|
| Summary and compaction | `summarizer` | Existing effective summarizer resolution |
| Project wiki | `summarizer`, `llmConnection`, `llmAggregator`, `llmRouter`, or a compatible linked Wiki Workflow Agent | Existing Wiki maintenance through the effective summarizer LLM |
| Automatic memory | `summarizer`, `llmConnection`, `llmAggregator`, or `llmRouter` | Existing extraction through the effective summarizer LLM |
| Memory embeddings | `llmConnection` | Existing `embedding_llm_service`, then local embeddings |
| Attachment OCR | `llmConnection` | Existing agent configuration or `PAWFLOW_MARKITDOWN_OCR_LLM_SERVICE`, then the standard attachment pipeline |
| Skill learning | `summarizer`, `llmConnection`, `llmAggregator`, or `llmRouter` | Existing skill loop through the effective summarizer LLM |
| Conversation titles | `llmConnection`, `llmAggregator`, or `llmRouter` | Existing `title_llm_service` configuration, otherwise disabled |
| Content and package review | `summarizer`, `llmConnection`, `llmAggregator`, or `llmRouter` | Existing review through the effective summarizer LLM |

An agent definition may declare `automation_roles`. PawFlow currently consumes
an agent target only for `project_wiki`, and only when the selected conversation
instance uses the Workflow Agent runtime with an exact `flow_fqn`. Other roles
do not advertise agent targets until their owner component has an executable
agent contract.

The historical `summarizer_binding` storage key remains authoritative for the
summary and compaction role. The unified UI reads and writes that key through
the existing summarizer binding API, so existing conversations require no
migration.

## Web actions

- `linked_services_list` returns every role, its explicit/broken state, and its
  currently compatible visible targets.
- `linked_service_link` validates and stores one explicit role target.
- `linked_service_unlink` removes one target and immediately restores the
  PawFlow default.

All three actions require a conversation ID. Linking also requires a known role
and either a scoped service reference or a compatible conversation agent
instance.
