---
name: refactor
description: Invoke the refactor-planner agent for complex multi-step codebase reorganizations
argument-hint: "[optional: describe what you want to reorganize]"
allowed-tools:
  - Task
  - Read
  - Glob
  - Grep
---

Invoke the refactor-planner agent to handle complex refactoring tasks.

The agent can:
- Analyze codebase structure and suggest reorganization
- Create and execute refactoring plans (JSON format)
- Move modules, symbols, and rename across the codebase
- Validate imports after restructuring

## Usage

Pass the user's refactoring goal to the refactor-planner agent:

```
Use Task tool with subagent_type="refactory:refactor-planner" and the user's request as prompt.
```

If user provides a specific plan or description, pass it to the agent.
If user just runs `/refactor` without arguments, have the agent analyze the codebase and suggest improvements.
