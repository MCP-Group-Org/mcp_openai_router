# Repository Guidelines

The main instructions for contributors:  
always respond and explain in **Russian**, start with a **basic solution** without premature optimizations,  
and for each new task create a `[task_name].md` file containing the description, approach, and a checklist ([]/[x]) with pause points for testing, analysis, or commits.

## Contributor Instructions

- Always communicate and comment in **Russian** when working in this repository.  
- Start with a **minimum viable product (MVP)** and postpone advanced improvements until the base logic is verified.  
- For every new task, create a `[task_name].md` file that includes:  
  - the problem statement,  
  - the proposed approach,  
  - a checklist ([]/[x]) with control points (tests, analysis, commits).  
- Project structure and module organization are described in `README.md`.

## Coding Style & Naming Conventions

- Use **Python 3.12 + FastAPI**, 4-space indentation, PEP 8 import order, and full type annotations (see `ToolSpec`, Pydantic models).  
- Tool and handler names must follow `snake_case`; constants use `UPPER_SNAKE_CASE`.  
- Define new tools through `ToolSchema` / `ToolSpec` and register them with `_register_tool_*` functions for consistency.

## Testing Guidelines

- Add tests to `tests/test_mcp_router.py` and name them `test_<behavior>`.  
- Use fixtures to manage global objects (e.g., `ACTIVE_SESSIONS`).  
- Mock OpenAI clients by monkeypatching the `_create_openai_client` function to keep tests offline.  
- Protect new functionality with pytest assertions that validate JSON-RPC and MCP metadata (`toolCalls`, `isError`, etc.).

## Commit Documentation Rules

> Commit message format:
>
>
> `full_branch_name`.`title` (eng)
> `description` (ru)
>

Where:  

- Commit documentation is prepared **without** a separate `.md` task file.  
- `full_branch_name` — the full branch name, e.g., `feature/new_feature`, not just `new_feature`.  
- `title` — a short English summary (3–7 words).  
- `description` — a detailed explanation of the changes in Russian.  
- Write documentation for **indexed files**, not already committed changes.  
- The resulting text should be placed into `./.git/COMMIT_EDITMSG`.
