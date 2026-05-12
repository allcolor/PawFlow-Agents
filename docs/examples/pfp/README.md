# PFP Examples

These directories are starter `.pfpdir` templates for PawFlow packages.
They intentionally use `ed25519:REPLACE_WITH_PUBLIC_KEY`; replace it with the public key returned by `/pfp key-create`, then build with `/pfp build` or `manage_package(action="build", ...)`.

Examples:

- `tool_echo.pfpdir`: installs a Python subprocess tool named `echo`.
- `service_provider_image.pfpdir`: installs a `packageRuntime` service provider with a `generate` operation.
- `flow_task_uppercase.pfpdir`: installs a flow processor type named `exampleUppercase`.
- `flow_bundle_uppercase.pfpdir`: installs both `exampleUppercase` and a runnable flow using it.
- `inter_pfp_base_tool.pfpdir`: installs `examples.text-core/tool:normalize_text`.
- `inter_pfp_service_consumer.pfpdir`: installs a service provider that depends on and calls the base tool package.

Each template follows the same conventions expected from third-party packages:

- object ids are stable `type:name` values;
- code entrypoints declare a runner explicitly;
- host calls must be listed in `allowed_tools` or `allowed_services` before install;
- secrets, when needed, are declared by logical package name and bound to existing PawFlow secret keys at install time;
- flows and flow tasks are installed as normal PawFlow resources after consent.

Build workflow:

```text
/pfp key-create
# copy the public key into pfp.json, store the private key in an environment variable
/pfp build docs/examples/pfp/tool_echo.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp inspect docs/examples/pfp/tool_echo.pfpdir/dist/examples.echo-tool-0.1.0.pfp
/pfp install docs/examples/pfp/tool_echo.pfpdir/dist/examples.echo-tool-0.1.0.pfp --include tool:echo --force
```

Registry workflow:

```text
/pfp registry add https://example.com/pawflow/index.json --name example --trusted
/pfp search echo
/pfp inspect examples.echo-tool@0.1.0
/pfp install examples.echo-tool@0.1.0 --include tool:echo --force
```

Inter-PFP workflow:

```text
/pfp build docs/examples/pfp/inter_pfp_base_tool.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp install docs/examples/pfp/inter_pfp_base_tool.pfpdir/dist/examples.text-core-0.1.0.pfp --include tool:normalize_text --force
/pfp build docs/examples/pfp/inter_pfp_service_consumer.pfpdir --key-env PAWFLOW_PFP_SIGNING_KEY
/pfp inspect docs/examples/pfp/inter_pfp_service_consumer.pfpdir/dist/examples.text-service-0.1.0.pfp
/pfp install docs/examples/pfp/inter_pfp_service_consumer.pfpdir/dist/examples.text-service-0.1.0.pfp --include service_provider:text-cleaner --force
```

For publication workflow details, see `docs/PFP_PUBLISHER_GUIDE.md`.
