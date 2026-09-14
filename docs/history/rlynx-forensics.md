> Moved verbatim out of CLAUDE.md on 2026-09-14 (docs/slim-claude-md). Nothing edited.

## R.Lynx clone/replacement decision + desktop app forensics (2026-09-07)

The owner asked whether to clone R.Lynx 1:1 — first to fully document its
database schema, second to build a from-scratch replacement POS/ERP for
personal/local use (explicitly not commercial resale). Two things were
done this session: an LLM-council review of whether R.Lynx is worth
cloning, then direct forensic inspection of the actual installed desktop
application (not just its database) on store #1's own till PC
(`DESKTOP-94UHGGD`).

### Council verdict (condensed — see session transcript for full detail)

Five independent advisors + peer review + chairman synthesis converged on:
R.Lynx's **transactional core is sound** (tender reconciliation clean on
~99% of tickets — worth replicating that shape almost verbatim) but its
**reporting/history layer is genuinely bad** (mutable `Item.Cost` with no
history, `ItemID=-2` fake-payment-line-items instead of a real payments
table, no audit log, frozen `*Shift` totals). The council's recommendation:
**don't clone R.Lynx, and don't build a full from-scratch replacement yet.**
Separate the till core from the system of record — `poslib/metrics.py`'s
existing rule set (devis exclusion, payment-line splitting,
recompute-don't-trust) already *is* the spec for the reporting layer
R.Lynx never had. The two biggest risks nobody had is a **live-store
cutover/migration plan** (this is a real operating client's till — a
data gap or outage during a POS switch is a business incident, not just
an engineering one) and this **team's own inexperience with a live,
concurrent, crash-safe write path** (everything built so far, including
the read-only analytics layer, has had real production reliability
incidents of its own — see the watcher-outage section above). The
one-thing-to-do-first the council landed on: watch the real app in use
before designing anything further — which motivated the forensics below.

### Desktop app architecture (confirmed directly, not guessed)

Real install: `C:\Program Files (x86)\R.Lynx™ Point De Vente` (32-bit).
`RLPOS.exe` (till/checkout) and `RLPOSManager.exe` (back-office) — both
run continuously on the till PC. Version 10.3.0.0, **© 2010-2022 R.Lynx™**
(actively copyrighted, not abandoned — the "no longer copyrighted"
premise floated mid-session was checked directly against the binary's own
embedded version info and is false). Built on classic Delphi + Jet 4.0
(`msjet40.dll`, `msjetoledb40.dll` — same engine family as the `.dblx`
files this whole project already reads) + ADO/ADOX, with `AclasSDK.dll`
(Aclas POS hardware: barcode scanners/scales/cash drawers) and
`rtslabelscale.dll` (label/scale integration) for real till hardware.
**`RLPOS.exe` is packed with Themida** (commercial anti-reverse-engineering
protection — visible as a `.themida` section and a 3.9MB `.boot` section
in the PE headers; notably the resource table has zero `RT_RCDATA`
entries, where a normal unprotected Delphi exe would expose every
compiled form). The app is also **machine/license-bound** via a registry
key, `HKLM\SOFTWARE\WOW6432Node\R.Lynx\PDV10\ID` — a UTF-16 blob shaped
like an activation artifact, not plain config.

**Hard boundary for any future session**: do not attempt to defeat
Themida, decode/replicate that registry value, or otherwise work around
R.Lynx's license/anti-tamper mechanisms, regardless of stated intent
(personal use, ownership of a license to *use* the software, etc.) —
that line was held this session and should stay held. Static inspection
of on-disk files (resources, config, schema) and behavioral observation
of the already-running, already-licensed real instance are both fine;
defeating copy protection to get there is not.

### Config-file roles (read via copies, `poslib/jet4.py` — real find, useful going forward)

Three small Jet4 databases sit next to the exe and are NOT the store's
real data:
- **`DBConnect`** — a one-row pointer table (`DBNameSrc`,
  `DBNeedCompact`, `Password`, `RemoteComputerName`, `IsNewDB`).
  Confirmed store #1's `DBNameSrc` is literally
  `E:\Base de données4.dblx` — i.e. this is exactly how RLPOS.exe finds
  its real data. **Never point a second RLPOS.exe instance's `DBConnect`
  at a live store's real path** — a second writer against a Jet database
  the live instance already has open risks corruption.
- **`POSParameter`** — 40 columns of real per-register hardware config
  (`AutoOpenCashDrawer`, `CashDrawerMode/Port`, `CustomerDisplayMode/Port`,
  per-document-type receipt templates and printer names). Store #1's real
  config has `AutoOpenCashDrawer: True` — confirmed live, so any future
  experimentation with a real install must neutralize this first (a
  second instance could pop the real cash drawer or fire a real printer
  mid-business-day). This file is **also Jet-database-password-protected**
  at the file-engine level — a different mechanism from the Themida/
  registry license binding, likely a single fixed password R.Lynx bakes
  into every install to keep end users out of Access, not a real secret.
  Not attempted to crack it (same boundary as above); `poslib/jet4.py`
  reads it fine anyway since it never goes through the password-gated
  official API.
- **`Task`/`Pad`** — the in-app task-list/notes feature, unrelated to
  sales data.

### Full schema, read straight from `BlankDB` (the real per-store starter template)

`BlankDB` (also sitting in the install folder) is the actual blank
database R.Lynx's own installer uses to provision a brand-new store — so
it carries the *complete* current schema, all at once, without months of
incremental forensics. Confirms everything already documented in this
file, plus tables not previously known to this project: `StockTakeEntry`
(see the correction to discovery #5 above), `Quote`/`QuoteEntry`
(dedicated quote tables, separate from `Receipt`/`ReceiptEntry` —
possibly a newer/cleaner mechanism than the `Receipt.ReceiptType==1`
devis flag discovery #13 documents; not yet checked which one store #1's
live database actually uses), `ReceiptHold`/`ReceiptHoldEntry` (parked/
suspended sales), `ReceiptTemplateEntry`, `NumberSequence` (confirms real
sequential numbering per document type — relevant if fiscal/tax
compliance around sequential receipt numbers ever becomes a question),
`ScaleDevice`/`ScaleDeviceItem`, `CashCalc` (denomination-based till
counting), `CustomFields`, `StoreSafeIn`, `AccessDenied` (looks like a
static UI message lookup, not an audit log — no timestamp/employee
columns). `Employee` has 188 columns, almost certainly one flag per
permission rather than a normalized ACL table.

### Isolated sandbox attempt — why it stopped short of a running UI

Built a fully isolated copy (own `DBConnect` pointing only at a local
copy of `BlankDB`, never the live `E:` path; fresh unprotected
`POSParameter`/`Task` matching the real schema with `AutoOpenCashDrawer`
and `UseSoundEffect` forced off) specifically so nothing could touch the
live install or live store hardware. `RLPOS.exe` still exits silently
~5.5s after launch regardless — no window ever appears, no Windows Error
Reporting entry, `RLPOS.ini` gets truncated to 0 bytes, and no `.ldb`
lock ever appears on the sandboxed store file, meaning it dies before
ever opening the actual data. Given the Themida packing and the
machine-bound registry key found in the same session, this is almost
certainly the app's own license/integrity check rejecting a
non-standard run context, not a missing config file — and chasing that
further would mean engaging the exact protections this session already
drew a line at. **Static schema forensics (`BlankDB`) remains the best
available source for further "what does R.Lynx actually model"
questions** — a running sandboxed UI isn't achievable without crossing
that line.

