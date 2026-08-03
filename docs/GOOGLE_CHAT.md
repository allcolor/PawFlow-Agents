# Google Chat integration

The `google_chat.google_chat_agent:1.0.0` flow exposes a Google Chat HTTP app
endpoint at `/webhooks/google-chat/<instance-id>`. Deploy it in user scope. The
deploying PawFlow user is always the execution principal; webhook JSON cannot
select or override that identity.

Required deployment parameters are the service-account JSON, the expected ID
token audience, and the owner's immutable Google Chat `users/...` identifier.
Configure the generated HTTPS endpoint as the Chat app interaction endpoint.
Keep `google_chat_audience_type=endpoint_url` (recommended) when Google Chat is
configured with the HTTPS endpoint URL as its authentication audience. Select
`project_number` only when the Chat app configuration uses a Cloud project
number; PawFlow then verifies the Chat service-account JWT certificate and
issuer explicitly.

Spaces are denied by default. Adding the app records the immutable `spaces/...`
ID as `pending`. In that space, the configured owner can run:

```text
/gchat status
/gchat allow <conversation_id>
/gchat deny
```

`read_only` is mandatory for collective spaces and is enforced per turn, independently of the
conversation's webchat permission mode. It uses a fail-closed tool allowlist and
also blocks delegation. Direct messages are owner-only and require a
`direct_conversation_id` that belongs to the configuring PawFlow owner and has a
selected agent.

For every accepted group message, PawFlow stores the real Google actor, space,
thread, and message IDs as provenance while authorizing the turn as the flow
owner. Replies are posted to the originating thread one `new_message` event at a
time. The final `done.response` is only used when no live assistant message was
already delivered. Google attachment resources are downloaded with app
credentials and materialized in FileStore before agent submission.
