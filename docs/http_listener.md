# HTTP Listener — Shared HTTP Server for Flows

PawFlow provides a shared HTTP listener service that lets multiple flows handle incoming HTTP requests on the same port, with method+URL pattern routing, authentication validation, and custom response control.

## Architecture

```
HTTP Client                          PawFlow
    │                                  │
    │  GET /api/users/42               │
    ├─────────────────────────────────►│
    │                                  │
    │     ┌─────────────────────────┐  │
    │     │ HTTPListenerService     │  │
    │     │ (shared, port 9090)     │  │
    │     │                        │  │
    │     │  Route Registry:       │  │
    │     │  GET /api/users/{id}   │──┼──► Flow A (httpReceiver)
    │     │  POST /api/orders      │──┼──► Flow B (httpReceiver)
    │     │  * /webhooks/{src}     │──┼──► Flow C (httpReceiver)
    │     └─────────────────────────┘  │
    │                                  │
    │  HTTP 200 {"name": "Alice"}      │
    │◄─────────────────────────────────┤
    │                                  │
```

## Components

### 1. HTTPListenerService (`services/http_listener_service.py`)

Shared service (singleton per port). Starts a threaded HTTP server and dispatches incoming requests to registered flows.

**Config:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| host | string | 0.0.0.0 | Bind address |
| port | int | 9090 | Listen port |
| request_timeout | float | 30.0 | Seconds before 504 Gateway Timeout |

**Key features:**
- Route registry with `{param}` path parameters
- Route conflict detection (two flows can't register the same route)
- 404 when no route matches
- 504 when flow doesn't respond in time
- 503 when service shuts down with pending requests

### 2. httpReceiver Task (`tasks/io/http_receiver.py`)

Self-triggering source task. Registers routes on the listener and converts HTTP requests into FlowFiles.

**Config:**
| Parameter | Type | Description |
|-----------|------|-------------|
| service_id | string | ID of the HTTPListenerService |
| routes | array | Route definitions (see below) |

**Route definition:**
```json
{
  "method": "GET",
  "pattern": "/api/users/{id}",
  "relationship": "GET:/api/users/{id}"
}
```

**FlowFile attributes set:**
| Attribute | Description |
|-----------|-------------|
| http.request.id | Correlation ID (required for response) |
| http.method | HTTP method |
| http.path | Request path |
| http.query | Query string |
| http.header.* | Request headers (lowercase keys) |
| http.path.* | Path parameters from URL pattern |
| http.remote.addr | Client IP |
| route.relationship | Determines connection routing |

### 3. handleHTTPResponse Task (`tasks/io/handle_http_response.py`)

Sends the HTTP response back through the listener service.

**Config:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| service_id | string | — | ID of the HTTPListenerService |
| status_code | int | 200 | Default HTTP status code |
| content_type | string | application/json | Default Content-Type |
| headers | object | {} | Default response headers |

**FlowFile attribute overrides:**
| Attribute | Description |
|-----------|-------------|
| http.response.status | Override HTTP status code |
| http.response.header.* | Override/add response headers |
| http.response.body | Override response body (instead of FlowFile content) |

### 4. validateHTTPAuth Task (`tasks/io/validate_http_auth.py`)

Validates HTTP authentication. On success, passes through with auth attributes. On failure, auto-responds with 401/403.

**Config:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| auth_service_id | string | — | ID of the HTTPAuthService |
| listener_service_id | string | — | ID of HTTPListenerService (for auto-response) |
| auto_respond | bool | true | Auto-send 401/403 on failure |
| header_name | string | authorization | Header to validate |

### 5. HTTPAuthService (`services/http_auth_service.py`)

Validates Bearer tokens and Basic auth credentials.

**Config:**
| Parameter | Type | Description |
|-----------|------|-------------|
| auth_type | string | "bearer", "basic", or "custom" |
| tokens | array | Valid bearer tokens |
| users | object | username: password mapping |
| realm | string | HTTP auth realm |

## Example Flows

### Hello World

```json
{
  "id": "http-hello-world",
  "services": {
    "http_listener": {
      "type": "httpListener",
      "config": { "port": 9090 }
    }
  },
  "tasks": {
    "http_in": {
      "type": "httpReceiver",
      "parameters": {
        "service_id": "http_listener",
        "routes": [
          {"method": "GET", "pattern": "/api/helloworld/{who}"}
        ]
      }
    },
    "set_body": {
      "type": "replaceText",
      "parameters": {
        "replacement": "<h1>Hello ${http.path.who}!</h1>",
        "strategy": "always"
      }
    },
    "send_response": {
      "type": "handleHTTPResponse",
      "parameters": {
        "service_id": "http_listener",
        "content_type": "text/html"
      }
    }
  },
  "relations": [
    {"from": "http_in", "to": "set_body", "type": "success"},
    {"from": "set_body", "to": "send_response", "type": "success"}
  ]
}
```

### REST API with Auth

```json
{
  "id": "secure-api",
  "services": {
    "http_listener": {
      "type": "httpListener",
      "config": { "port": 9090 }
    },
    "auth": {
      "type": "httpAuthValidator",
      "config": {
        "auth_type": "bearer",
        "tokens": ["my-secret-token"]
      }
    }
  },
  "tasks": {
    "http_in": {
      "type": "httpReceiver",
      "parameters": {
        "service_id": "http_listener",
        "routes": [
          {"method": "GET", "pattern": "/api/data"},
          {"method": "POST", "pattern": "/api/data"}
        ]
      }
    },
    "validate_auth": {
      "type": "validateHTTPAuth",
      "parameters": {
        "auth_service_id": "auth",
        "listener_service_id": "http_listener"
      }
    },
    "get_handler": { "type": "log", "parameters": {"message": "GET request"} },
    "post_handler": { "type": "log", "parameters": {"message": "POST request"} },
    "send_response": {
      "type": "handleHTTPResponse",
      "parameters": { "service_id": "http_listener" }
    }
  },
  "relations": [
    {"from": "http_in", "to": "validate_auth", "type": "success"},
    {"from": "validate_auth", "to": "get_handler", "type": "GET:/api/data"},
    {"from": "validate_auth", "to": "post_handler", "type": "POST:/api/data"},
    {"from": "get_handler", "to": "send_response", "type": "success"},
    {"from": "post_handler", "to": "send_response", "type": "success"}
  ]
}
```

## GUI Integration

The HTTP Listener tasks appear in the **IO** category in the flow editor:
- **httpReceiver** — drag to canvas as the entry point
- **handleHTTPResponse** — connect at the end of your flow
- **validateHTTPAuth** — optional, place between receiver and handlers

The **Services** panel shows `httpListener` and `httpAuthValidator` services.

## How It Works Internally

1. **httpReceiver** registers routes on the shared `HTTPListenerService`
2. When an HTTP request arrives, the service matches it against the route registry
3. If matched, a `PendingRequest` is created and the HTTP handler thread blocks
4. The matched callback enqueues a FlowFile into the receiver's internal queue
5. The ContinuousFlowExecutor scheduler detects `has_pending_input() == True`
6. The FlowFile flows through the DAG with `route.relationship` for routing
7. **handleHTTPResponse** calls `service.submit_response()` to unblock the HTTP handler
8. The HTTP response is sent to the client
