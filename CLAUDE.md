# CLAUDE.md

Guidance for Claude Code (and other contributors) working in this repository.

## Project context

Atlas Signal is an experimental, general-purpose news intelligence and event-tracking project. See `README.md` for the project description and current status.

- Canada–EU economic cooperation is the likely first tracking experiment, but the system must not become Canada–EU-specific. Keep topic- and region-specific logic out of core code.
- No language, framework, or architecture has been chosen yet. Do not assume one.

## Working rules

1. **Inspect before changing.** Read the relevant files and check the current state of the repository before editing anything.
2. **Prefer small, testable increments.** Make one focused change at a time, each one possible to verify.
3. **No infrastructure without a demonstrated need.** Do not add databases, queues, containers, services, CI pipelines, or cloud resources until a concrete requirement calls for them.
4. **Do not silently make major architectural decisions.** Choices about language, framework, storage, data model, project structure, or external services must be proposed to and approved by the user first. Explain the trade-offs.
5. **Do not add dependencies casually.** Justify each new dependency, prefer the standard library where it is reasonable, and ask before adding anything significant.
6. **Preserve room for the project to expand.** Avoid designs that lock the project into a single source, topic, region, or use case.
7. **Distinguish implemented from planned functionality.** Documentation, comments, and reports must not describe planned features as if they exist.
8. **Run relevant tests and checks before commits.** If no tests or checks exist for the change, say so.
9. **Report exactly what changed.** After each task, list the files created, modified, or deleted, and summarize the changes accurately, including anything skipped or failed.
10. **Never commit sensitive material.** This includes secrets, credentials, API keys, tokens, `.env` files, private financial information, and Amazon or other account documents. If such material shows up in the working tree, do not stage it; tell the user instead.
11. **Do not commit or push unless explicitly instructed.** Approval for one commit or push does not carry over to later ones.

## Environment notes

- Development is on Windows 11. Both PowerShell and Git Bash are available.
- Python is available through the `py` launcher (`py` / `py -3.14`). The bare `python` and `python3` commands are not usable.
- The Git remote `origin` is `https://github.com/jpeckham-hue/atlas-signal.git`. The default branch is `main`.
