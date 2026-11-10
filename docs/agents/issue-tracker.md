# Issue tracker: global beads

Issues for this repo are **beads** (`bd`) issues in one machine-global beads workspace — not GitHub
Issues. `brunnseb/open-wearables` on GitHub is the code remote only; never `gh issue create` here.

## Where it lives

- Workspace: `/home/media/beads/.beads` — one Dolt database (server mode) shared by every repo on this
  machine. `~/.local/bin/bd` exports `BEADS_DIR`, so `bd` resolves it from any checkout or worktree.
- Issue ids look like `bd-42` (prefix `bd`). This repo's issues carry the label `project:open-wearables`.
- Do not create a per-repo `.beads/` directory; the global workspace is the only one.

## Commands

| Task | Command |
| --- | --- |
| Open work for this repo | `bd ready --label project:open-wearables` |
| Read one issue | `bd show bd-42` |
| Claim it | `bd update bd-42 --claim` |
| File a ticket | `bd create "<title>" -p 1 -t task -l project:open-wearables -d "<body>"` |
| Close it | `bd close bd-42` |
| Chain work | `bd dep add bd-43 --depends-on bd-42` |
| Durable project facts | `bd remember "..."` |
| End of session | `bd sync` |

Add `--json` to anything you parse. Label filters differ by command: `-l/--label` on `ready`/`list`,
`--labels` on `create`.

## Lifecycle

When asked to implement a bead: `bd update bd-42 --claim` before editing, `bd close bd-42` once the
issue's acceptance check passes, and `bd remember "..."` for anything a later session must not
rediscover. Never leave a worked bead open with no claim.

## Conventions

- One bead per ticket: title is the deliverable, `-d` body holds the user story and acceptance
  criteria. Step-by-step checklists live in the PR, not in the tracker.
- Priority is `-p 0..4` (0 highest); triage state is labels (see `triage-labels.md`); who owns it is
  the claim (`--claim`), not a label.
- Name the bead in the commit subject and PR body (`bd-42: add provider sync hook`) so code and
  tracker cross-reference.
- Never track work in markdown TODO lists, plan files or a `devdocs/` handoff; the beads database is
  the record and it survives a session.

## GitHub

PRs still go to GitHub via `gh pr create`; the body names the bead it implements. Issues are
beads-only.
