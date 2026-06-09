# Marketplace and Package Registries

PawFlow has two marketplace-style surfaces:

- **PFP package registries** for signed `.pfp` artifacts that can contain agents, prompts, skills, themes, task definitions, flows, service definitions, tools, service providers, flow tasks, task providers, and UI extensions.
- **External skill marketplace import** for Agent Skills from supported sources such as Codex/OpenAI skills, Claude/Anthropic plugin marketplaces, HermesHub, and OpenClaw GitHub tree URLs.

For the package format and runtime security model, see [PawFlow Packages](PFP_PACKAGES.md). For package development and publishing workflows, see [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md) and [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md).

## PFP Registries

A PFP registry is a static JSON index hosted on HTTPS, a repository raw URL, or an internal artifact host. Registry entries describe package id, version, artifact URL, artifact byte size, SHA-256 pin, developer key, tags, and object ids.

Registry metadata is discovery and provenance data only. PawFlow still requires explicit download confirmation for remote artifacts, verifies size and SHA-256 pins when present, verifies the `.pfp` Ed25519 signature, verifies `pfp.lock.json`, and shows a selectable install or update plan before writing resources.

Common commands:

```text
/pfp registry add https://example.com/pawflow/index.json --name example --trusted
/pfp registry list
/pfp search media provider
/pfp inspect community.wavespeed@1.0.0 --confirm-download
/pfp install community.wavespeed@1.0.0 --include service_provider:image --confirm-download
/pfp update community.wavespeed@1.1.0 --confirm-download
/pfp uninstall community.wavespeed
```

The web Resources sidebar exposes the same package actions: registry management, registry search, remote package confirmation, object selection, required secret bindings, install/update diffs, and uninstall.

## External Skill Import

Skills can be searched and imported independently from `.pfp` packages:

```text
/skill search --source all code review
/skill import --source codex openai/skills/path/to/skill
/skill import --source github --force owner/repo@main:path/to/skill
/skill assign @agent @skill-name
```

The web Resources sidebar exposes the same flow from the Skills repository `+` button: choose **Create** for a local skill or **Import** to resolve a GitHub repository, select a branch/tag and a directory containing `SKILL.md`, review it, then import it. Review decisions are not web-only: when an import is blocked or needs human review, the response includes a `/skill import --force ...` confirmation command that can be run from any client, including Telegram and CLI-style clients.

Agent tools expose the same path through `manage_resource(action="search_marketplace", resource_type="skill", ...)`, `manage_resource(action="resolve_import_source", resource_type="skill", ref="owner/repo", ...)`, and `manage_resource(action="import_marketplace", resource_type="skill", ...)`.

Imported skills are treated as untrusted content. PawFlow fetches a bounded skill directory, requires a UTF-8 root `SKILL.md`, rejects unsafe paths and oversized packages, records provenance and package hashes, and runs the configured skill review before writing. Package scripts and `allowed-tools` declarations are stored as package data only; they are never executed automatically and never grant tool permissions.

## PFP Package Workflow

Developers work in unsigned `.pfpdir` source directories and use `dev-load` for fast local iteration:

```text
/pfp dev-load ./my-provider.pfpdir --include service_provider:image --secret api_key=my_provider_key
/pfp dev-unload dev.my-provider
```

Release artifacts are signed `.pfp` files:

```text
/pfp key-create
/pfp build ./my-provider.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp inspect ./my-provider.pfpdir/dist/dev.my-provider-0.1.0.pfp
/pfp install ./my-provider.pfpdir/dist/dev.my-provider-0.1.0.pfp --include service_provider:image
```

`/pfp export` can bundle existing resources into a `.pfpdir`. When exporting agents, PawFlow includes referenced assigned skills so the package remains installable elsewhere.

## Security Boundaries

- Signed `.pfp` install requires a valid signature and lock file.
- Install writes only selected objects from the review plan.
- Required secrets are bound by name during install; secret values are never stored in package files or install records.
- Code-bearing package objects execute in the selected relay through the package runtime runner, not inside the PawFlow server process.
- Calls back into PawFlow tools or services must use `pfp.call_tool(...)` or `pfp.call_service(...)` and must match grants declared in the package manifest and accepted during install.
- Package-qualified grants require the referenced package and object to be installed before runtime access is allowed.

## Related Docs

- [PawFlow Packages](PFP_PACKAGES.md)
- [PFP Developer Guide](PFP_DEVELOPER_GUIDE.md)
- [PFP Publisher Guide](PFP_PUBLISHER_GUIDE.md)
- [Slash Commands](SLASH_COMMANDS.md)
- [Agent System](AGENT_SYSTEM.md)
