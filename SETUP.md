# Setup — getting this running on a (new) PC

This is the checklist for picking this project back up on a machine that has
never seen it before. Follow it top to bottom.

**This is not the same document as `README.md`.** `README.md` explains how to
*use* the finished dashboard as the shop owner. This one is for *you*,
setting up the development environment so you (or Claude Code) can keep
building.

---

## 0. What you're setting up

Three things, in order:

1. **Git and Python** — the tools this project needs, that don't come with a
   fresh Windows install.
2. **The repo itself**, cloned from GitHub.
3. **The cross-PC sync** — already built into the repo, needs nothing extra
   from you here, but it's worth knowing what it does before you start.

---

## 1. Install Git

Check first — it might already be there:

```powershell
where.exe git
```

If nothing prints, install it:

```powershell
winget install --id Git.Git -e --source winget
```

**Close and reopen your terminal / VS Code after installing.** Windows does
not refresh a running terminal's PATH — a terminal open before the install
will not see `git` even though it's installed. This bit us once already; a
fresh terminal window fixes it.

Verify in the *new* window:

```powershell
git --version
```

---

## 2. Install Python 3.12

```powershell
winget install --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements
```

Same rule applies — open a fresh terminal afterward. Verify:

```powershell
python --version
```

If `python` still isn't found in a genuinely new terminal, it's usually at:

```
%LOCALAPPDATA%\Programs\Python\Python312\python.exe
```

---

## 3. Clone the repository

Pick a location (this example uses the Desktop, but anywhere is fine):

```powershell
cd $env:USERPROFILE\Desktop
git clone https://github.com/rachadmihoubi/pos-tool.git
cd pos-tool
```

That's it — the clone brings everything, including the git-committed
`.claude/settings.json` that makes step 5 below automatic.

---

## 4. Open it and let Claude Code catch you up

Open the `pos-tool` folder in VS Code and start a Claude Code session there.

Two things happen automatically, because they're committed in the repo:

- **`CLAUDE.md`** loads as project context. It's written specifically to brief
  a fresh session — what's built, three non-obvious data-quality discoveries
  from the original build (each verified against the real database, not
  guessed), what's still outstanding, and the one thing that's
  machine-specific (`config.yaml`'s database path — see step 6).
- **The sync hook fires** — see below.

You shouldn't need to explain any history yourself. If something in
`CLAUDE.md` looks stale or wrong, that's worth flagging back to Claude Code
so it can be corrected — it's meant to be kept current, not left to rot.

---

## 5. The cross-PC sync (already active, nothing to do)

`.claude/settings.json` contains a `SessionStart` hook: every time Claude
Code opens in this folder, it silently runs `git pull --ff-only` first. That
means whichever PC you pushed from most recently, this one picks up
automatically — no manual `git pull` needed.

It's genuinely silent and safe:
- Not a git repo / no upstream / offline → does nothing, no error shown
- Can't fast-forward (real conflict) → does nothing, leaves it for you to
  resolve by hand rather than guessing
- Never force-pulls, never creates a surprise merge commit

Because this is a **fresh clone**, the hook is already in `.claude/settings.json`
*before* Claude Code starts — so it activates immediately, first session, no
extra step. (This is different from when a hook gets added to an
*already-running* session, which needs `/hooks` or a restart to pick up —
not a concern here.)

**Before you switch away from this PC**, push your work with one command:

```powershell
.\tools\sync.ps1
```

Stages everything, commits (timestamped message, or pass your own:
`.\tools\sync.ps1 "finished the diagnostics rules"`), pushes. Safe to run
with nothing changed — it just says so and stops. This direction is
deliberately a command you run, not a silent hook, so nothing gets committed
without you seeing it first.

---

## 6. Point the tool at the real POS database on this PC

`config.yaml` → `database.path` is the one setting that's genuinely
machine-specific — it won't be the same drive/folder on every PC. Open
`config.yaml` and check:

```yaml
database:
  path: "E:/Base de données4.dblx"
```

Change it to wherever the `.dblx` file actually is on *this* machine. If it's
wrong, the tool fails loudly with a clear error naming the missing file — you
can't miss it, and nothing bad happens if you get it wrong temporarily.

---

## 7. Install and verify

```powershell
.\setup.bat
```

Creates the virtual environment, installs dependencies, does a first read of
the database. Takes a minute; needs internet for this step only.

Then confirm everything actually works:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Should show `84 passed` (or more, if new tests were added since). If it
fails on a "grows" check (revenue, row counts), something is being lost — see
`CLAUDE.md` for what those checks mean and their known tolerances.

Finally, start the dashboard:

```powershell
.\start.bat
```

---

## Quick reference

| I want to... | Run |
|---|---|
| Get the latest from the other PC | Nothing — happens automatically on session start |
| Push my work before switching PCs | `.\tools\sync.ps1` |
| Re-read the POS database right now | `.venv\Scripts\python.exe -m poslib.etl --force` |
| Run the test suite | `.venv\Scripts\python.exe -m pytest tests -q` |
| Start the dashboard | `.\start.bat` |
| Understand *why* something in the code is the way it is | Read `CLAUDE.md` first |
| Understand how to *use* the finished dashboard | Read `README.md` |

---

## Troubleshooting

**`git` or `python` not found right after installing** — you're in a
terminal that predates the install. Close it, open a new one. This is not a
bug in the install, it's how Windows environment variables work for
already-running processes.

**The sync hook doesn't seem to be pulling anything** — check you're
actually behind: `git log --oneline -3` on both machines. If this PC's
`HEAD` already matches what you pushed, there's nothing to pull — that's
correct, not broken.

**`git pull --ff-only` left things unpulled with no error shown** — that
means it couldn't fast-forward (you have local commits the other PC doesn't
know about, or a real divergence). Run `git status` and `git log --oneline
--all --graph` by hand to see what's going on; the hook deliberately doesn't
try to resolve this for you.

**Everything else** — see the Troubleshooting section in `README.md`, and
remember: nothing in this project can write to the live POS database. The
source `.dblx` file is always opened read-only and always copied before
being read, so there's no setup mistake here that can damage the shop's
actual data.
