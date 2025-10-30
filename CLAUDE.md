# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

read REAMDE.md

## 1. Language and Communication

- All code comments, commit descriptions, and documentation must be in Russian.

---

## 2. Workflow and Human Control

### 2.1. Step-by-Step Execution

Each task is divided into steps.  
After each step, the process pauses for human review.

### 2.2. Task Plan

All work tasks follow the procedure below:

- A task plan file is created with a name reflecting the task context, conventionally **[plan-task]**.
- The task plan file contains:
  - task description,
  - proposed solution approach,
  - step-by-step implementation plan;
- Task execution begins only after approval by a human;
- Tasks are performed step by step, with completed steps marked in the plan file;
- After each completed step, execution stops for human review/confirmation.

### 2.3. Commits Are Made by a Human

The agent does not perform `git commit` or `push` actions on its own.

### 2.4. Scope Limitation

The agent modifies only the current project scope, unless otherwise specified in the task plan file.

### 2.5. Plan Updates

If conditions or goals change — the plan is updated and reapproved before continuing.

---

## 3. Coding Standard

- Follow the project style: indentation, import order, naming, type annotations.
- Naming:
  - functions and variables — `snake_case`;
  - constants — `UPPER_SNAKE_CASE`;
  - classes and models — `CamelCase`.
- Write simple and readable code with short functions and explicit dependencies.
- Document public functions and interfaces (purpose, input parameters, result).

---

## 4. Commits

When requested by the user, prepare a commit message in the following format:

- **Title (EN)** — short imperative action.  
- **Description (RU)** — what was changed, why, and how to verify.  

Insert the generated description into the file `./.git/COMMIT_EDITMSG`.
