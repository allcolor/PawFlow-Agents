# PawFlow Expression Language Reference

PawFlow provides a powerful expression language using `${...}` syntax for dynamic value resolution in task parameters, flow configurations, and service settings. Expressions are resolved at runtime and support chainable operations, nested expressions, and multi-pass resolution.

## Syntax

```
${key}
${key:op1:op2("arg")}
${scope.key:op1:op2("arg1","arg2"):op3}
${:generator}
${:generator("arg")}
```

- **key** -- the variable name to resolve
- **:op** -- chainable operations applied left-to-right (pipe pattern)
- **("arg")** -- arguments to an operation (quoted strings)
- **:generator** -- generators produce values without a key (empty scope)

## Scopes and Resolution Cascade

When you write `${key}`, PawFlow searches for the value through a cascade of scopes, returning the first match found.

### Parameter Cascade

Resolution order for parameters (first match wins):

1. **Flow parameters** -- task config and FlowFile attributes (`${attribute_name}`)
2. **Conversation parameters** -- per-conversation parameters stored in conversation metadata
3. **User parameters** -- per-user parameters (`config/users/{username}/parameters.json`)
4. **Global parameters** -- shared across all users (`config/global_parameters.json`)
5. **Environment variables** -- OS environment (`${PATH}`, `${HOME}`, etc.)

### Reserved parameters

The deploy layer injects reserved flow parameters that flows can reference but
should not override:

- **`${_instance_id}`** -- the unique deployment instance id (e.g.
  `github-ci-autofix__a1b2c3`), stable across restarts. Use it to mint
  per-instance, collision-free values such as a webhook route
  `/webhooks/github/${_instance_id}` so multiple deploys of the same flow
  (different projects/users) never clash on a shared resource.

### Secret Cascade

Secrets are checked before parameters (first match wins):

1. **Conversation secrets** -- per-conversation encrypted secrets
2. **User secrets** -- per-user encrypted secrets (`config/users/{username}/secrets.json`)
3. **Global secrets** -- shared encrypted secrets (`config/global_secrets.json`)

The overall resolution order is: **secrets cascade** -> **parameters cascade** -> **runtime variables**.

### Forcing a Specific Scope

Use `:!important(scope)` to bypass the cascade and resolve from exactly one scope:

```
${api_key:!important(global)}     -- only global parameters
${api_key:!important(user)}       -- only user parameters
${api_key:!important(conv)}       -- only conversation parameters
${api_key:!important(env)}        -- only OS environment
${api_key:!important(flow)}       -- only flow parameters
```

If the key is not found in the specified scope, the expression remains unresolved.

## Storage and Management

| Scope | Storage Location | How to Set |
|-------|-----------------|------------|
| Global secrets | `config/global_secrets.json` | Runtime UI |
| Global parameters | `config/global_parameters.json` | Runtime UI |
| User secrets | `config/users/{user}/secrets.json` | `/add-secret name value` or `store_secret` tool |
| User parameters | `config/users/{user}/parameters.json` | `/add-variable name value` |
| Conversation parameters | conversation metadata | API / agent tools |
| Conversation secrets | conversation metadata (encrypted) | API / agent tools |
| Flow parameters | flow JSON `parameters` section | flow definition |
| FlowFile attributes | set by tasks at runtime | `updateAttribute` task |
| Environment variables | OS environment | system configuration |

Conversation parameters can also override transient FileStore TTLs used by chat artifacts. Set `webchat_upload_ttl_seconds` or `attachment_ttl_seconds` to control user-uploaded attachment retention, and `screenshot_ttl_seconds` or `webchat_screenshot_ttl_seconds` to control screenshot retention. Values are seconds and are clamped to at least 60 seconds.

## Operators Reference

Operations are chained with `:` and applied left-to-right. Arguments are passed in parentheses with quoted strings.

### String Operations

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `upper` | -- | Convert to uppercase | `${name:upper}` -> `ALICE` |
| `lower` | -- | Convert to lowercase | `${name:lower}` -> `alice` |
| `trim` | -- | Strip leading/trailing whitespace | `${input:trim}` |
| `ltrim` | -- | Strip leading whitespace | `${input:ltrim}` |
| `rtrim` | -- | Strip trailing whitespace | `${input:rtrim}` |
| `capitalize` | -- | Capitalize first letter | `${name:capitalize}` -> `Alice` |
| `title` | -- | Title case | `${text:title}` -> `Hello World` |
| `reverse` | -- | Reverse the string | `${text:reverse}` -> `dcba` |
| `length` | -- | String length (as string) | `${name:length}` -> `5` |
| `count` | -- | Length of string or list | `${items:count}` -> `3` |

### Substring and Replace

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `substr` | `(start)` or `(start, end)` | Extract substring | `${text:substr(0,5)}` -> first 5 chars |
| `replace` | `(old, new)` | Replace all occurrences | `${url:replace("http","https")}` |
| `replace_regex` | `(pattern, replacement)` | Regex replace | `${text:replace_regex("\\d+","N")}` |
| `append` | `(suffix)` | Append text | `${base:append("/api")}` -> `https://example.com/api` |
| `prepend` | `(prefix)` | Prepend text | `${path:prepend("/root")}` |
| `pad_left` | `(width)` or `(width, char)` | Left-pad to width | `${id:pad_left(5,"0")}` -> `00042` |
| `pad_right` | `(width)` or `(width, char)` | Right-pad to width | `${code:pad_right(10," ")}` |

### Split, Join, and Indexing

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `split` | `(separator)` | Split string into list (default `,`) | `${csv:split(",")}` |
| `join` | `(separator)` | Join list into string (default `,`) | `${items:join(" - ")}` |
| `index` | `(n)` | Get nth element from list | `${list:index(0)}` -> first element |
| `first` | -- | Get first element of list | `${items:first}` |
| `last` | -- | Get last element of list | `${items:last}` |

### Conditional Operations

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `default` | `(fallback)` | Use fallback if value is empty | `${key:default("N/A")}` |
| `equals` | `(value)` | Test equality (returns boolean) | `${status:equals("active")}` |
| `not_equals` | `(value)` | Test inequality (returns boolean) | `${mode:not_equals("test")}` |
| `contains` | `(substring)` | Test if string contains substring | `${text:contains("error")}` |
| `starts_with` | `(prefix)` | Test if string starts with prefix | `${url:starts_with("https")}` |
| `ends_with` | `(suffix)` | Test if string ends with suffix | `${file:ends_with(".json")}` |
| `matches` | `(regex)` | Test if string matches regex | `${email:matches("@.*\\.com$")}` |
| `is_empty` | -- | Test if string is empty/whitespace | `${value:is_empty}` |
| `then` | `(value)` | If previous result is `true`, return value | `${x:equals("y"):then("YES")}` |
| `else` | `(value)` | If previous result is `false`, return value | `${x:equals("y"):else("NO")}` |

The `then`/`else` operators work with boolean results from comparison operators:

```
${status:equals("active"):then("ON"):else("OFF")}
```

### Encoding and Hashing

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `base64_encode` | -- | Encode to Base64 | `${data:base64_encode}` |
| `base64_decode` | -- | Decode from Base64 | `${encoded:base64_decode}` |
| `url_encode` | -- | URL-encode (percent encoding) | `${query:url_encode}` |
| `url_decode` | -- | URL-decode | `${encoded:url_decode}` |
| `hash_md5` | -- | MD5 hash (hex) | `${content:hash_md5}` |
| `hash_sha256` | -- | SHA-256 hash (hex) | `${content:hash_sha256}` |

### Type Conversion

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `to_int` | -- | Convert to integer (returns `"0"` on failure) | `${count:to_int}` |
| `to_float` | -- | Convert to float (returns `"0.0"` on failure) | `${price:to_float}` |
| `to_bool` | -- | Convert to boolean string (`"true"`/`"false"`) | `${flag:to_bool}` |

`to_bool` recognizes `true`, `1`, `yes`, `on` (case-insensitive) as `"true"`, everything else as `"false"`.

### JSON

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `json_get` | `(path)` | Extract value from JSON by dot-separated path | `${response:json_get("data.items.0.name")}` |

The path supports both object keys and array indices: `"key.nested.0.field"`.

### Date and Time

| Operator | Arguments | Description | Example |
|----------|-----------|-------------|---------|
| `now` | `(format)` | Current time (default `%Y-%m-%dT%H:%M:%S`) | `${:now("%Y-%m-%d")}` -> `2026-04-07` |
| `format_date` | `(format)` | Format an ISO date string (default `%Y-%m-%d`) | `${timestamp:format_date("%d/%m/%Y")}` |
| `add_days` | `(n)` | Add N days to an ISO date string | `${date:add_days(7)}` |
| `timestamp` | -- | Current Unix timestamp (seconds) | `${:timestamp}` -> `1712505600` |

### Generators

Generators produce values and do not require an input key. Use an empty scope:

| Generator | Arguments | Description | Example |
|-----------|-----------|-------------|---------|
| `uuid` | -- | Random UUID (full) | `${:uuid}` -> `a1b2c3d4-e5f6-...` |
| `uuid_short` | -- | Short UUID (12 hex chars) | `${:uuid_short}` -> `a1b2c3d4e5f6` |
| `random_int` | `(min, max)` | Random integer in range (default 0-100) | `${:random_int(1,10)}` -> `7` |
| `random_string` | `(length)` | Random alphanumeric string (default 16) | `${:random_string(8)}` -> `kR4mPq2x` |
| `now` | `(format)` | Current time formatted | `${:now("%H:%M")}` -> `14:30` |
| `timestamp` | -- | Current Unix timestamp | `${:timestamp}` -> `1712505600` |

## Nested Expressions

Arguments to operators can contain `${...}` expressions, which are resolved before the operation is applied:

```
${status:equals("active"):then(${active_label}):else(${inactive_label})}
```

The parser correctly handles balanced braces, so deeply nested expressions work:

```
${x:equals("y"):then(${a:upper}):else(${b:lower})}
```

## Multi-Pass Resolution

If a resolved value itself contains `${...}` expressions, PawFlow re-resolves the result. This continues recursively up to 10 levels deep.

Example: if `template` resolves to `Hello ${name}`, and `name` resolves to `Alice`, then `${template}` ultimately produces `Hello Alice`.

## Usage in Configuration

Expressions can be used in most task parameter values:

```json
{
  "type": "inferLLM",
  "parameters": {
    "api_key": "${openai_api_key}",
    "model": "${model_name:default(\"gpt-4o\")}",
    "system_prompt": "You are ${agent_role:default(\"a helpful assistant\")}."
  }
}
```

## Common Patterns

### Default values

```
${api_key:default("sk-placeholder")}
```

### Conditional branching

```
${env:equals("production"):then("https://api.prod.com"):else("http://localhost:8080")}
```

### URL construction

```
${base_url:append("/api/v"):append(${api_version:default("2")})}
```

### String formatting

```
${username:upper:prepend("USER_"):append("_"):append(${:uuid_short})}
```

### Safe JSON extraction

```
${response_body:json_get("data.items.0.id"):default("unknown")}
```

### Chaining split/join

```
${tags:split(","):join(" | ")}
```

### Hashing for deduplication

```
${content:hash_sha256}
```

### Date math

```
${start_date:add_days(30):format_date("%Y-%m-%d")}
```

### Force environment variable

```
${DATABASE_URL:!important(env)}
```

### Generate unique identifiers

```
${:uuid_short}
${:random_string(32)}
```

## LazyResolveDict

PawFlow provides `LazyResolveDict`, a dict wrapper that resolves `${...}` expressions on every `.get()` call. Values are never cached -- changes to parameters or secrets are picked up immediately. This is used internally for service and task configuration dicts.

## Escaping

To include a literal `${...}` in a string (for example, in a task definition prompt that will be resolved later at assign time), escape the dollar sign:

```
\${variable_name}
```

The backslash prevents resolution during the current pass, and the `\` is stripped, leaving `${variable_name}` for later resolution.

## Unresolved expressions

If an expression cannot be resolved in any scope, it is returned **verbatim**, with its operators preserved rather than stripped. Text that merely resembles an expression -- for example a shell parameter expansion (a default-substitution or a suffix-removal) embedded in file content or a script -- is therefore never silently altered by the resolver.
