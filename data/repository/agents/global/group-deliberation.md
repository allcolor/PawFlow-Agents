---
description: Bounded multi-agent deliberation backed by the first-party group workflow.
parameters: {}
runtime_defaults:
  kind: workflow
  workflow:
    flow_fqn: pawflow.agents.group-deliberation:1.0.0
    input_port: group_request
    terminal_port: group_terminal
    preempt_policy: queue
    allowed_effects:
      - resource.read
    parameters: {}
---

You coordinate a bounded PawFlow agent group. The workflow, not this prompt,
owns member selection, budgets, private-context exclusion, and terminal output.
