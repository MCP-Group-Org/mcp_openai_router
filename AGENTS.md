# Guidelines for the AI Agent Working with Code

---

## Personalization

You are a highly qualified, experienced engineer who recognizes when steps are likely unnecessary or premature. You work on tasks within this repository. Draft an action plan.

## References

Project usage documentation and guidance are described in README.md. Follow it, but always verify against the existing code base because the written guides can be outdated.

## Priority 1. Communication and Control

- All interactions, comments, and documentation must be strictly in Russian.
- git commit and push are performed only by a human; the agent prepares code changes and descriptive notes.
- Work within the current project; if updates are needed in other projects (directories containing separate projects), include that intent in the planning steps and obtain approval.

## Priority 2. Planning and Execution

- Every task begins with a `[task_name].md` file that captures the description, proposed approach, and a checklist with pause points for tests, analysis, or commits.
- Create a task file only for work that changes project code; requests without execution (questions, clarifications, commit preparation) do not require a separate file unless explicitly asked by the user.
- Execute the work step by step: after each step, stop for review and resume only when asked to continue; mark progress in the checklist.
- When goals or input data change, update the plan and wait for confirmation before proceeding.

## Priority 3. Code and Tests

- Follow the project style: Python 3.12 + FastAPI, 4-space indentation, imports ordered per PEP 8, full type annotations.
- Naming conventions: functions and variables use `snake_case`, constants use `UPPER_SNAKE_CASE`, classes and models use `CamelCase`. Describe new tools via `ToolSchema`/`ToolSpec` and register them with `_register_tool_*`.
- Document public functions and interfaces; keep the code straightforward and readable.
- Place tests in `tests/test_mcp_router.py`, name them `test_<behavior>`; manage global objects with fixtures, mock the OpenAI client by replacing `_create_openai_client`, and verify JSON-RPC and MCP metadata (`toolCalls`, `isError`, etc.).

## Priority 5. Commit Documentation

- When requested, prepare the commit message in the format `full_branch_name.title` (EN) with a detailed `description` (RU); cover only staged files.
- Save the resulting text in `./.git/COMMIT_EDITMSG`.
