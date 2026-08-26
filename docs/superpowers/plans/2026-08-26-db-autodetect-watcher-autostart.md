# DB Auto-Detect + Silent Watcher Auto-Start Implementation Plan

> **STATUS: NOT STARTED.** This is the next active plan — Component 2 of
> CLAUDE.md's "Customer distribution" build order. `packaging/setup.iss`
> has no diff yet; none of Task 1/2's steps below have been executed.
> Prerequisites (Component 1 packaging, Component 4 Cloudflare REST push)
> are both done — see the other two plan files in this directory.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Setup.exe` produce a fully working, silently-running, remote-publishing install with zero typed/edited configuration from a non-technical shop owner — closing the two gaps the prior packaging plan left open: the database path still requires hand-editing `config.yaml`, and nothing auto-starts at boot.

**Architecture:** Both gaps are closed entirely inside `packaging/setup.iss`'s Pascal Script `[Code]` section and its `[Run]`/`[UninstallRun]` sections — no new Python file, no new HTTP route, no first-run browser page. A custom wizard page shown during install tries to auto-detect the POS's `.dblx` file, falls back to a native "browse for file" dialog, and writes the chosen path straight into `%LOCALAPPDATA%\Shop Analysis\config.yaml` before the installer finishes (`ssPostInstall`) — so the file is already correct before anything ever tries to read it. A second, independent addition to `[Run]`/`[UninstallRun]` transplants the exact `schtasks /create ... /sc onlogon` pattern `install-startup.bat` already uses successfully on this dev PC, pointed at `ShopAnalysis.exe --watcher` (never `app.py`'s dashboard — the owner views the store from his phone via the existing Cloudflare Pages push, not by opening a browser on the till PC).

**Tech Stack:** Inno Setup 6 Pascal Script (`[Code]` section), the existing `schtasks.exe` Task Scheduler CLI, no new Python code.

## Global Constraints

- **Never write to the source `.dblx` file** — this plan only ever *reads* candidate `.dblx` paths to offer them as the config value; it never opens, copies, or touches the POS database itself. (project CLAUDE.md's one rule that overrides everything else)
- **Only the watcher auto-starts, never `app.py`'s Flask dashboard.** The owner's own words, 2026-08-26: "local dashboard isn't important to me... the end goal is always viewing the dashboard online." `watcher.py`'s `_remote_push_due()`/`_run_remote_push()` already rebuild the cache and push to Cloudflare Pages entirely on their own timer, with zero dependency on `app.py` running.
- **Never overwrite an existing `config.yaml`.** A second run of the installer (upgrade/repair) must not clobber a store's already-configured, already-working settings. Every write in this plan is gated on the target file not already existing.
- **`database.path` placeholder text to match exactly:** `C:/CHANGE-ME/point-this-at-your-database.dblx` (verbatim from `config.template.yaml`'s `database:` block, currently at line 17).
- **`%LOCALAPPDATA%\Shop Analysis` is the writable config root** — confirmed in `poslib/paths.py`'s `user_data_dir()` (Inno constant: `{localappdata}\Shop Analysis`). `{app}` (the Program Files install folder) is read-only at runtime per that same file's docstring, so nothing here writes there except during install.
- **`config.template.yaml` and `.env.example` are already bundled** into the PyInstaller onedir output at `{app}\config.template.yaml` / `{app}\.env.example` (confirmed via `packaging/pos-tool.spec`'s `datas` list and a real `dist/ShopAnalysis/` build) — this plan copies from there, it does not need to add them to `[Files]`.
- **Out of scope, decided now, not left implicit:** enabling `remote.enabled: true` and setting the correct `cloudflare_project_name` per store is a one-time technical step **rachad** performs by hand once per store (editing that store's `config.template.yaml` before compiling *that store's* `Setup.exe`, per the multi-store design's Component 4/5 notes) — never something this installer prompts the non-technical owner for. Not built here.
- **Out of scope:** Component 3 (auto-update via GitHub Releases) and Component 5 (multi-store hub + cross-store stock search) remain separate future plans, per `docs/superpowers/specs/2026-08-25-installer-updates-multistore-design.md`'s "Suggested build order."
- **No unit tests for `.iss` changes** — same precedent as the prior plan's Task 7 (Inno Setup script, verified by actually building and running the installer, not pytest). Each task's verification is a real `iscc` build plus a real run and file/registry inspection.

---

## File Structure

- **Modify: `packaging/setup.iss`** — the only file this plan touches.
  - New `[Code]` section: custom wizard page for database location (auto-detect + browse), config.yaml bootstrap-and-patch logic.
  - New entries in `[Run]` and `[UninstallRun]`: watcher auto-start scheduled task create/delete.

No other file changes. `poslib/config.py`'s existing `_bootstrap_if_frozen()` is left untouched — it remains the safety net for any case where the installer's own config.yaml write is skipped (e.g. `.env` bootstrap, which this plan does not touch, still happens there on first launch).

---

### Task 1: Database auto-detect wizard page, writes `config.yaml` at install time

**Files:**
- Modify: `packaging/setup.iss` (add `[Code]` section)

No unit test — verified by building with `iscc` and running the resulting `Setup.exe`, then inspecting the real `config.yaml` it produces.

**Interfaces:**
- Produces: a working `%LOCALAPPDATA%\Shop Analysis\config.yaml` with `database:` → `path:` set to a real, existing file, present on disk before `[Run]` entries (including Task 2's scheduled-task creation) execute.
- Consumes: `{app}\config.template.yaml` (already bundled, confirmed present in `dist/ShopAnalysis/`), the literal placeholder line `path: "C:/CHANGE-ME/point-this-at-your-database.dblx"`.

- [ ] **Step 1: Add the `[Code]` section to `packaging/setup.iss`**

Add this at the end of the file, after the existing `[Run]` section:

```pascal
[Code]
var
  DatabasePage: TWizardPage;
  DatabaseEdit: TNewEdit;
  DatabaseBrowseButton: TNewButton;
  DatabaseStatusLabel: TNewStaticText;

// Best-effort only - there is no confirmed single "standard" R.Lynx
// install location (this dev PC's own real database lives on the
// Desktop, not under Program Files), so this is a short list of
// plausible folders, not a guarantee. The Browse button is always the
// reliable path.
function DetectDatabasePath(): String;
var
  Candidates: TArrayOfString;
  I: Integer;
  FindRec: TFindRec;
  Folder: String;
begin
  Result := '';
  SetArrayLength(Candidates, 5);
  Candidates[0] := ExpandConstant('{commonpf}') + '\R.Lynx';
  Candidates[1] := ExpandConstant('{commonpf32}') + '\R.Lynx';
  Candidates[2] := 'C:\R.Lynx';
  Candidates[3] := ExpandConstant('{userdesktop}');
  Candidates[4] := ExpandConstant('{userdocs}');

  for I := 0 to GetArrayLength(Candidates) - 1 do
  begin
    Folder := Candidates[I];
    if DirExists(Folder) then
    begin
      if FindFirst(Folder + '\*.dblx', FindRec) then
      begin
        try
          Result := Folder + '\' + FindRec.Name;
          Exit;
        finally
          FindClose(FindRec);
        end;
      end;
    end;
  end;
end;

procedure UpdateDatabaseStatusLabel();
begin
  if DatabaseEdit.Text = '' then
    DatabaseStatusLabel.Caption :=
      'No database file selected yet. Click Browse and pick the file your ' +
      'point-of-sale software saves to.'
  else
    DatabaseStatusLabel.Caption := 'Using: ' + DatabaseEdit.Text;
end;

procedure DatabaseBrowseButtonClick(Sender: TObject);
var
  FileName: String;
begin
  FileName := DatabaseEdit.Text;
  if GetOpenFileName('Select your point-of-sale database file', FileName,
     ExtractFileDir(FileName), 'Database files (*.dblx)|*.dblx|All files (*.*)|*.*',
     'dblx') then
  begin
    DatabaseEdit.Text := FileName;
    UpdateDatabaseStatusLabel();
  end;
end;

procedure InitializeWizard();
begin
  DatabasePage := CreateCustomPage(wpSelectDir,
    'Locate your point-of-sale database',
    'Shop Analysis reads from this file to build your dashboard.');

  DatabaseStatusLabel := TNewStaticText.Create(DatabasePage);
  DatabaseStatusLabel.Parent := DatabasePage.Surface;
  DatabaseStatusLabel.Left := 0;
  DatabaseStatusLabel.Top := 0;
  DatabaseStatusLabel.Width := DatabasePage.SurfaceWidth;
  DatabaseStatusLabel.AutoSize := False;
  DatabaseStatusLabel.WordWrap := True;
  DatabaseStatusLabel.Height := ScaleY(32);

  DatabaseEdit := TNewEdit.Create(DatabasePage);
  DatabaseEdit.Parent := DatabasePage.Surface;
  DatabaseEdit.Left := 0;
  DatabaseEdit.Top := DatabaseStatusLabel.Top + DatabaseStatusLabel.Height + ScaleY(8);
  DatabaseEdit.Width := DatabasePage.SurfaceWidth;
  DatabaseEdit.ReadOnly := True;

  DatabaseBrowseButton := TNewButton.Create(DatabasePage);
  DatabaseBrowseButton.Parent := DatabasePage.Surface;
  DatabaseBrowseButton.Left := 0;
  DatabaseBrowseButton.Top := DatabaseEdit.Top + DatabaseEdit.Height + ScaleY(8);
  DatabaseBrowseButton.Width := ScaleX(100);
  DatabaseBrowseButton.Height := ScaleY(23);
  DatabaseBrowseButton.Caption := 'Browse...';
  DatabaseBrowseButton.OnClick := @DatabaseBrowseButtonClick;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DatabasePage.ID then
  begin
    if DatabaseEdit.Text = '' then
      DatabaseEdit.Text := DetectDatabasePath();
    UpdateDatabaseStatusLabel();
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DatabasePage.ID then
  begin
    if (DatabaseEdit.Text = '') or (not FileExists(DatabaseEdit.Text)) then
    begin
      MsgBox('Please click Browse and select your point-of-sale database file ' +
             '(it ends in .dblx) before continuing.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure WriteDatabaseConfig(const DBPath: String);
var
  ConfigDir, ConfigFile, TemplateFile, EscapedPath: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  ConfigDir := ExpandConstant('{localappdata}\Shop Analysis');
  ConfigFile := ConfigDir + '\config.yaml';
  TemplateFile := ExpandConstant('{app}\config.template.yaml');

  if FileExists(ConfigFile) then
    Exit; // never overwrite an existing, already-configured store

  ForceDirectories(ConfigDir);

  if not FileCopy(TemplateFile, ConfigFile, False) then
    Exit; // nothing more we can do here; app's own bootstrap remains the fallback

  EscapedPath := DBPath;
  StringChangeEx(EscapedPath, '\', '/', True);

  if LoadStringsFromFile(ConfigFile, Lines) then
  begin
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Pos('CHANGE-ME/point-this-at-your-database.dblx', Lines[I]) > 0 then
        Lines[I] := '  path: "' + EscapedPath + '"';
    end;
    SaveStringsToFile(ConfigFile, Lines, False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    WriteDatabaseConfig(DatabaseEdit.Text);
end;
```

- [ ] **Step 2: Build the installer**

Run: `"C:\Users\RACHAD\AppData\Local\Programs\Inno Setup 6\ISCC.exe" packaging/setup.iss`
Expected: exits 0, no compiler errors. Pascal Script errors surface as compile failures with a line number — fix any before continuing.

- [ ] **Step 3: Run the installer on this dev PC and verify the wizard page**

Run `dist-installer\Setup.exe`. On the new "Locate your point-of-sale database" page (shown right after choosing the install folder):
- Confirm the status label and Browse button render.
- Since this dev PC's real database lives at `C:/Users/RACHAD/Desktop/Base de données4.dblx` (per `CLAUDE.md`, not under any of the auto-detect candidate folders), auto-detect is expected to find nothing — confirm the edit box stays empty and clicking Next without browsing shows the "Please click Browse..." error.
- Click Browse, navigate to the real database file, select it, confirm the edit box and status label update, then click Next and finish the install.

- [ ] **Step 4: Verify the written config.yaml**

Run: `Get-Content "$env:LOCALAPPDATA\Shop Analysis\config.yaml" | Select-String "path:"`
Expected: the `database:` block's `path:` line shows the real path just selected, forward-slashed (e.g. `path: "C:/Users/RACHAD/Desktop/Base de données4.dblx"`), not the `CHANGE-ME` placeholder.

- [ ] **Step 5: Verify the no-overwrite guard**

Without deleting `config.yaml`, run `Setup.exe` again and repeat the browse step with a different (or the same) file. After it finishes, re-run Step 4's `Get-Content` check.
Expected: the `path:` value is **unchanged** from Step 4 — confirms `WriteDatabaseConfig`'s existing-file guard works, so a repair/upgrade install never clobbers a real store's settings.

- [ ] **Step 6: Uninstall to leave the dev PC clean**

Uninstall via "Add or Remove Programs" (or `"C:\Program Files\Shop Analysis\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART` — ask the user for confirmation first per this session's ledger note, since a prior automated uninstall attempt was blocked by the permission classifier). `%LOCALAPPDATA%\Shop Analysis\config.yaml` is not removed by uninstall (matches existing precedent — user data outlives the app); note this in the task report but do not delete it programmatically.

- [ ] **Step 7: Commit**

```bash
git add packaging/setup.iss
git commit -m "feat(packaging): auto-detect or prompt for the POS database path during install"
```

---

### Task 2: Silent watcher auto-start via Task Scheduler, baked into the installer

**Files:**
- Modify: `packaging/setup.iss` (add to `[Run]` and add new `[UninstallRun]` section)

No unit test — verified by building, installing, and inspecting the real Task Scheduler entry with `schtasks /query`.

**Interfaces:**
- Consumes: nothing from Task 1 (independent addition to the same file — order of the two tasks does not matter functionally, since `[Run]` entries execute in file order and Task 1's `ssPostInstall` config write happens before *any* `[Run]` entry regardless of where in `[Run]` this task's entry is placed).
- Produces: a Task Scheduler entry named `Shop Analysis - Watcher` that launches `ShopAnalysis.exe --watcher` (headless, `console=False` per `packaging/pos-tool.spec` — confirmed no window ever appears) at every logon, removed cleanly on uninstall.

- [ ] **Step 1: Add the scheduled-task create/delete entries to `packaging/setup.iss`**

Replace the existing `[Run]` section:

```ini
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Shop Analysis now"; Flags: nowait postinstall skipifsilent
Filename: "schtasks.exe"; Parameters: "/create /f /tn ""Shop Analysis - Watcher"" /tr ""\""{app}\{#MyAppExeName}\"" --watcher"" /sc onlogon /rl limited /delay 0000:30"; Flags: runhidden
```

Add a new section right after `[Run]`:

```ini
[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Shop Analysis - Watcher"""; Flags: runhidden
```

This transplants `install-startup.bat`'s proven `/sc onlogon /rl limited /delay 0000:30` pattern verbatim (same delay, same limited run-level so the task never runs elevated), swapping only the task name (`Shop Analysis - Watcher`, distinct from the old manual `.bat`-created `Shop Analysis - Dashboard`/`Shop Analysis - Digest` names so there's no collision on this dev PC) and the target (`{#MyAppExeName} --watcher` instead of `start-quiet.bat`, since only the watcher — not the dashboard — needs to auto-start per this project's actual goal). The digest scheduled task from `install-startup.bat` is deliberately **not** transplanted here — email/Telegram credentials aren't configured per-store yet (`.env` is empty on every machine per `CLAUDE.md`'s "What's left"), so there's nothing for it to send; add it in a later plan once a store has real digest credentials.

- [ ] **Step 2: Build the installer**

Run: `"C:\Users\RACHAD\AppData\Local\Programs\Inno Setup 6\ISCC.exe" packaging/setup.iss`
Expected: exits 0, no compiler errors.

- [ ] **Step 3: Install and verify the scheduled task was created**

Run `dist-installer\Setup.exe`, complete the wizard (browsing to the real database again, since this is a fresh install after Task 1's Step 6 uninstall).

Run: `schtasks /query /tn "Shop Analysis - Watcher" /fo list /v`
Expected: the task exists, `Task To Run` shows `"C:\Program Files\Shop Analysis\ShopAnalysis.exe" --watcher` (or wherever the install actually landed), `Scheduled Task State` is `Enabled`, `Logon Mode` reflects the onlogon trigger.

- [ ] **Step 4: Verify it actually runs silently at logon**

Log off and back on to this Windows account (or run `schtasks /run /tn "Shop Analysis - Watcher"` to fire it immediately as a faster proxy for the real trigger). Wait ~30-60 seconds, then:
- Confirm no window, taskbar entry, or console ever appears (`console=False` should hold; if a window does appear, that's a blocking finding — stop and report it, do not proceed to Step 5).
- Run: `Get-Content "$env:LOCALAPPDATA\Shop Analysis\logs\pos-tool.log" -Tail 20` — expect fresh watcher-startup log lines with a recent timestamp, confirming the scheduled task actually launched the process and it read the correct `config.yaml` from Task 1.
- Run: `Get-Process ShopAnalysis -ErrorAction SilentlyContinue` — expect one running process.

- [ ] **Step 5: Verify uninstall removes the scheduled task**

Uninstall via "Add or Remove Programs" (ask for confirmation first, per the same note as Task 1 Step 6).
Run: `schtasks /query /tn "Shop Analysis - Watcher"`
Expected: `ERROR: The system cannot find the file specified.` (or equivalent "task does not exist" message) — confirms `[UninstallRun]` cleaned it up. Also confirm the running watcher process from Step 4, if still alive, is left running until the next logoff (this plan does not kill the live process on uninstall — matches `install-startup.bat`'s own uninstall behavior, which only removes the scheduled task, not a currently-running instance).

- [ ] **Step 6: Commit**

```bash
git add packaging/setup.iss
git commit -m "feat(packaging): bake silent watcher auto-start into the installer via Task Scheduler"
```

---

## Self-Review

**1. Spec coverage:**
- Component 2 (spec lines 152-160, "scans standard R.Lynx install locations... pre-fill... browse dialog... writes into config.yaml"): covered by Task 1 in full — auto-detect scan, browse fallback, config.yaml write, gated to never clobber an existing config.
- "Silent auto-start baked into the installer, watcher not dashboard" (owner's reframing + CLAUDE.md's new subsection): covered by Task 2 in full — `[Run]`/`[UninstallRun]` schtasks entries targeting `--watcher`, verified headless via `console=False`.
- "Per-store Cloudflare enablement" (dispatch point 4): explicitly resolved as out-of-scope in Global Constraints, with the concrete reason and where it actually happens (rachad edits `config.template.yaml` before compiling each store's `Setup.exe`) — not left implicit.
- Component 3 (auto-update) and Component 5 (hub): explicitly deferred in Global Constraints, matching the plan's stated stopping condition.

**2. Placeholder scan:** No TBD/TODO. Both tasks contain complete, runnable Pascal Script and `.ini`-format `[Run]`/`[UninstallRun]` text, not descriptions of code to write. Verification steps use real commands with real expected output, not "add appropriate checks."

**3. Type consistency:** `DatabaseEdit.Text` (Task 1) is the only cross-task-relevant symbol, and Task 2 does not reference it — the two tasks are independent Pascal Script additions to the same file, confirmed to not collide (Task 1 adds `[Code]` at file end; Task 2 modifies `[Run]` and adds `[UninstallRun]`, both above where `[Code]` will sit). Function/procedure names (`DetectDatabasePath`, `WriteDatabaseConfig`, `DatabaseBrowseButtonClick`, `UpdateDatabaseStatusLabel`) are each defined once and referenced consistently within Task 1's own step; nothing in Task 2 depends on them.

No gaps found; nothing needed fixing beyond what's already reflected above.
