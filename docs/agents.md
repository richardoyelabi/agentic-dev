# Agents

## Agent Definitions

All agents are defined as YAML files in `src/agentic_dev/agents/definitions/`.

### Model Assignments

| Model | Agents | Rationale |
|---|---|---|
| **opus** | Feature Analyst, Architect, all QA agents, UAT | Deep reasoning, critical review, multi-document synthesis |
| **sonnet** | Input Processor, Sprint Planner, Frontend/Backend Dev, Integration | Structured transformation, code generation, cost-effective |

### Design & Architecture Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Input Processor | *(none)* | User input | Structured Input |
| Feature Analyst | Feature Analyst QA | Structured Input | Features Request |
| Architect | Architect QA | Features Request, Structured Input | Frontend Spec, Backend Spec, API Contract |
| Sprint Planner | Sprint Planner QA | Features Request, all specs | Sprint Plan |

### Frontend Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Frontend Developer | Frontend QA | Frontend Spec, API Contract, Sprint scope | Code in frontend/ repo |

### Backend Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Backend Developer | Backend QA | Backend Spec, API Contract, Sprint scope | Code in backend/ repo |

### Integration Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| Integration Agent | Integration QA | API Contract, Sprint Plan | Integration code + guide |

### QA Team

| Agent | QA Counterpart | Input | Output |
|---|---|---|---|
| UAT Agent | *(none — reports to user)* | All specs + Sprint Plan | UAT Report |

## Agent YAML Schema

```yaml
name: string
description: string
team: string
claude:
  model: opus|sonnet
  permission_mode: plan|bypassPermissions
  allowed_tools: [list]
  max_budget_usd: float
  use_bare_mode: true
  max_turns: 50
prompt_template: string
input_documents: [list]
output_documents: [list]
qa_agent: string|null
working_directory: string
constraints: [list]
```

## Prompt Templates

All templates are Jinja2 files in `src/agentic_dev/prompts/templates/`. Each template receives:

- `input_documents` — dict of document name → content
- `constraints` — list of constraint strings from the YAML definition
- `correction_mode` — bool, True when re-running after QA feedback
- `previous_output` — the agent's previous output (correction mode only)
- `qa_feedback` — QA agent's feedback (correction mode only)

Partials in `_partials/` provide reusable blocks for API contract context, sprint scope, and correction instructions.
