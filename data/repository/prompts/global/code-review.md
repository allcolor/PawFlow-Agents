---
title: Code Review
category: development
description: Ask for a focused code review on specific aspects
parameters:
  language:
    type: string
    default: Python
    description: Programming language
  focus:
    type: string
    default: bugs, security, and performance
    description: What to focus the review on
---

Review this ${language} code. Focus specifically on ${focus}.

Provide concrete suggestions with code examples where relevant.
