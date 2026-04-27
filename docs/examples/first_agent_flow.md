# Example: Agent-Created Daily Digest Flow

This example shows the intended PawFlow pattern: use an agent to design or maintain an automation, then let the deterministic flow engine run it on schedule.

## Goal

Create a daily digest that:

1. runs every morning;
2. fetches external data;
3. asks an LLM for a concise analysis;
4. sends the result to email, Slack, Discord, or another channel;
5. continues running without keeping an autonomous agent in the runtime loop.

## Prompt

```text
Create a flow that sends me a daily digest at 7 AM. Fetch the source data, summarize it with an LLM, and send the result by email. Use OAuth2 for the email service.
```

## Flow Shape

```text
cronTrigger
  -> fetchHTTP or executeScript
  -> transformJSON / executeScript
  -> inferLLM
  -> mergeContent
  -> sendEmail
```

## Minimal JSON Skeleton

```json
{
  "id": "daily-digest-email",
  "tasks": {
    "trigger": {
      "type": "cronTrigger",
      "parameters": { "schedule": "0 7 * * *" }
    },
    "fetch_data": {
      "type": "fetchHTTP",
      "parameters": { "url": "https://example.com/api/feed" }
    },
    "summarize": {
      "type": "inferLLM",
      "parameters": { "prompt": "Summarize this data in 5 bullet points." }
    },
    "send_email": {
      "type": "sendEmail",
      "parameters": { "auth_type": "oauth2" }
    }
  },
  "relations": [
    { "from": "trigger", "to": "fetch_data" },
    { "from": "fetch_data", "to": "summarize" },
    { "from": "summarize", "to": "send_email" }
  ]
}
```

Adjust parameters to match the exact task schemas in [Task Catalog](../tasks.md).

## Why This Pattern Matters

The agent is useful while designing, debugging, or updating the automation. The scheduled runtime is deterministic: the CRON trigger and flow tasks execute the same graph each run, with LLM calls only where explicitly modeled by tasks such as `inferLLM`.

## Useful Variants

- Replace `sendEmail` with `slackSend`, `discordSend`, or `telegramSend`.
- Add `tool.generate_image` to create a daily visual asset.
- Add `publishMessage` to publish the digest into a PawFlow conversation.
- Add `readConversation` when a flow needs to resume from prior agent context.
- Use `spawnAgent` for supervised work where an agent should review or enrich a step.
