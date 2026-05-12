# PFP Publisher Guide

This guide covers the publication side of PawFlow Packages: signing, registry indexes, release updates, and key rotation.

## Release Checklist

1. Create or update a `.pfpdir` source tree with `pfp.json` and files under `content/`.
2. Keep object ids stable across releases. Update and uninstall use ids such as `tool:name` and `service_provider:name` as durable keys.
3. Declare all package dependencies, inter-PFP grants, required secrets, runners, and provided capabilities in `pfp.json`.
4. Generate a signing key with `/pfp key-create` and put only the public key in `developer.public_key`.
5. Store the private key outside the repository, usually in an environment variable, and build with `/pfp build --key-env ENV_NAME`.
6. Inspect the signed `.pfp` before publishing and confirm capabilities, object selection, hashes, and update diff.
7. Upload the immutable `.pfp` artifact to a HTTPS URL.
8. Add or update the registry index entry with the package URL, package SHA-256, developer key, tags, and object ids.

## Registry Index

A PFP registry is a static JSON document. It can be hosted on any HTTPS server, repository raw URL, or internal artifact host.

```json
{
  "format": "pawflow.package.registry.v1",
  "registry": "example",
  "packages": [
    {
      "package": "examples.text-core",
      "version": "0.1.0",
      "description": "Text utilities for PawFlow packages",
      "pfp_url": "https://example.com/pfp/examples.text-core-0.1.0.pfp",
      "sha256": "sha256:REPLACE_WITH_ARTIFACT_HASH",
      "developer_key": "ed25519:REPLACE_WITH_PUBLIC_KEY",
      "tags": ["text", "utility"],
      "objects": ["tool:normalize_text"]
    }
  ]
}
```

`sha256` is a download pin. PawFlow verifies it when resolving a registry package, then still verifies the `.pfp` signature and lock file before install. Registry metadata is discovery/provenance data, not authority to bypass install consent.

## Publishing Versions

Publish each version as a new immutable `.pfp` file and add a distinct registry entry. Do not overwrite existing artifact URLs unless your artifact host guarantees byte-for-byte immutability.

Recommended versioning:

- Patch: compatible bug fixes, same objects and grants.
- Minor: new objects, optional parameters, or expanded capabilities.
- Major: removed objects, changed required secrets, changed grants, or incompatible runtime behavior.

When a release removes an object, `inspect` shows it in `update_diff` as `remove`; `update` removes that previously installed object if it is selected and not locally modified, unless `force` is used.

## Key Rotation

A package version is signed by the `developer.public_key` in that version's manifest. To rotate keys:

1. Generate a new key with `/pfp key-create`.
2. Publish a new package version whose manifest contains the new public key.
3. Update the registry entry for the new version with the new `developer_key` and SHA-256.
4. Keep old public keys visible in older registry entries so users can audit old versions.
5. Document the rotation in release notes or your registry page.

If a key is compromised, publish a new version signed with a fresh key and mark the old version as deprecated in your registry metadata or external release notes. PawFlow does not trust registry deprecation as policy enforcement; users still decide what to install.

## Trust Metadata

Users can add a registry with `--trusted`. This is a local consent/provenance label. It does not make registry data authoritative, does not disable package signature verification, and does not auto-install packages.

For publishers, this means your registry should make review easy: include stable URLs, SHA pins, developer keys, concise descriptions, tags, and object ids.

## Inter-PFP Packages

If package B calls package A, package B must declare both a dependency and an allowed grant. Example:

```json
{
  "dependencies": [
    {"package": "examples.text-core", "version": "^0.1.0", "object": "tool:normalize_text"}
  ],
  "objects": [
    {
      "id": "service_provider:text-cleaner",
      "type": "service_provider",
      "runner": "python_subprocess_host",
      "allowed_tools": [
        {"package": "examples.text-core", "version": "^0.1.0", "object": "tool:normalize_text"}
      ]
    }
  ]
}
```

The referenced package and object must be installed before the dependent object can be selected for install. At runtime, `PackageCapabilityBroker` re-checks the installed package/object before allowing the host call.
