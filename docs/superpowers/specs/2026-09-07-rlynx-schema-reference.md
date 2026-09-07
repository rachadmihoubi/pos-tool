# R.Lynx full schema reference

**Status:** first pass, complete for structure, not yet cross-checked
against any store's live `.dblx` for which tables/columns actually have
real data. Purpose: a single reference for the R.Lynx data model, as a
foundation for (a) a full understanding of what R.Lynx tracks, and (b)
eventually informing a from-scratch replacement — see
`CLAUDE.md`'s "R.Lynx clone/replacement decision + desktop app forensics"
section for the council verdict this doc supports (don't clone/rebuild
yet; this documentation work is the safe, valuable part of that effort).

## Provenance — what this is built from, and what it deliberately isn't

Every table below comes from `BlankDB`, the blank per-store starter
database bundled inside the R.Lynx 10.3.0.0 installer itself
(`C:\Program Files (x86)\R.Lynx™ Point De Vente\BlankDB` on store #1's
till PC), plus the three small local-machine config databases that sit
next to the app (`DBConnect`, `POSParameter`, `Task`/`Pad`). All four were
read via read-only copies through `poslib/jet4.py` — pos-tool's own
from-scratch Jet 4 reader — never through R.Lynx's own code, never
through the live `E:\Base de données4.dblx`, and nothing was written back
anywhere. This complements, not replaces, the empirical discoveries
already in `CLAUDE.md` (which come from a real store's live data and tell
you what's actually *populated* and how R.Lynx's business logic actually
*behaves* — this doc only tells you what the schema *can* hold).

**Known gap**: `BlankDB` is a template for a *new* store on the
*currently installed* R.Lynx version. Store #1's live `E:\Base de
données4.dblx` predates at least one table here (`StockTakeEntry` — see
CLAUDE.md discovery #5's correction) and may differ from the other two
stores' databases too if they were provisioned from different R.Lynx
versions. Treat every table below as "the schema supports this," not
"this store's real data has this."

## How to read the type column

Types are named the way `poslib/jet4.py` names them: `int32`, `byte`
(1-byte unsigned), `bool`, `float32`, `money` (8-byte currency, 4 decimal
places), `datetime`, `text(N)` (variable-length, N = max characters),
`memo` (unbounded text), `ole` (binary/OLE object — pictures; stripped by
pos-tool's own ETL, see `SKIPPED_COLUMN_TYPES`).

## Core transactional tables

### Receipt / ReceiptEntry — completed sales, quotes-via-flag, invoices
The single busiest table pair. `ReceiptType` distinguishes sale / devis
(quote) / invoice / fake-invoice (see CLAUDE.md discovery #13 for the
devis-exclusion rule already built on this). Tender is split into
`Cash`/`Cheque`/`Transfer`/`Other`/`CreditAccount` — the shape that makes
the ~99%-clean tender reconciliation possible (CLAUDE.md discovery #9).

**New pattern worth knowing**: `Receipt.OldAccount`/`NewAccount` —
a **before/after snapshot of the customer's account balance**, taken at
the moment of this transaction. This is the exact same idiom as
`PurchaseEntry.NewStock` (a stock snapshot after each purchase line,
CLAUDE.md discovery #2 on the AVCO work) — R.Lynx tracks "balance right
after this happened" as a point-in-time column rather than a proper
ledger. **This could be a much cheaper way to get a customer's point-in-
time balance than replaying the customer's whole transaction history** —
worth checking against a live database: does `NewAccount` actually track
reliably, the same reliability check CLAUDE.md discovery #2 already did
for `PurchaseEntry.NewStock`, before trusting it.

Also present and not yet used by pos-tool: `IsLoyaltyProgram`,
`TotalLoyaltyAmount`, `LoyaltyOffer`, `TotalQty`, `RecalledID`/
`RecallType`/`RecallResultID` (return/exchange linkage back to an
original receipt — worth checking whether this is how R.Lynx models a
refund against a specific prior sale).

- `ID, BatchID, RegisterID, ReceiptType, ReceiptNo, Reference, Comment,`
  `EmployeeID, CustomerID, Time, TotalCost, MarginRate, Margin,`
  `AccountPayment, SubTotal, DiscountRate, Discount, VAT, RecalledVAT,`
  `VATExempt, PriceLevelID, Total, Cash, Cheque, Transfer, Other,`
  `CreditAccount, ChangeCash, TotalMiscItemSale, TotalStandardItemSale,`
  `TotalMiscItemReturn, TotalStandardItemReturn, TotalDiscount, TotalVAT,`
  `TotalStamp, PayIn, PayOut, Stamp, StampRate, PaymentDue, PaymentTotal,`
  `RecallType, RecallResultID, RecalledID, ChequeNo, PaymentMode,`
  `OldAccount, NewAccount, CustomTitle, IsInvoiceMode, DateCreated,`
  `DateLastUpdated, TotalLoyaltyAmount, LoyaltyOffer, IsLoyaltyProgram,`
  `TotalQty`
- `ReceiptEntry`: `ID, ReceiptID, ItemID, RelationItemID, ItemName, Qty,`
  `Price, ItemPrice, Cost, DiscountRate, Discount, ExtPrice, VATRate,`
  `VATAmount, Amount, SerialNumber, MiscItemSale, StandardItemSale,`
  `MiscItemReturn, StandardItemReturn, TotalItemDiscount, TotalItemVAT,`
  `TotalItemMargin, ItemAccountPayment, Parcel, QtyPerParcel,`
  `IsLoyaltyPointItemOffer, PriceRestorePoint`
  (`SerialNumber` confirms per-line serial-number tracking is a real,
  if maybe unused, feature; `PriceRestorePoint` — undocumented, possibly
  ties to a price-override-then-undo flow, worth checking behaviorally.)

### Quote / QuoteEntry — a *second*, separate quote mechanism
Nearly column-identical to `Receipt`/`ReceiptEntry` (down to
`OldAccount`/`NewAccount`, tender columns, everything). **Open question**:
CLAUDE.md discovery #13 documents devis as `Receipt.ReceiptType == 1` —
but a fully separate `Quote` table also exists. Not yet checked which one
store #1's live database actually populates, or whether both are used for
different things (e.g. `Quote` for a formal saved quote vs. the `DV`
`Receipt.ReceiptType` flag for something else). Check this directly
against a live `.dblx` before assuming either table is unused.

### ReceiptHold / ReceiptHoldEntry — parked/suspended sales
A real "hold this half-finished sale, come back to it later" feature.
Also near-identical in shape to `Receipt`, plus `CustName`/`CustInfo`/
`EmployeeLoginName`/`HoldComment` (denormalized, presumably so a held
sale is self-describing without joining back to `Customer`/`Employee`).
Not something pos-tool has ever surfaced — a live-database check for real
rows here would confirm whether this feature is actually used day-to-day.

### ReceiptTemplateEntry — saved line-item templates
Same shape as `ReceiptEntry` again, keyed by `ReceiptID` — likely backs a
"save this basket as a reusable template" feature, not itself a sale.

### Batch — till sessions
`CashOpen/ChequeOpen/TransferOpen/OtherOpen/CreditAccountOpen` (opening
float), `*Shift` (mid-session — see CLAUDE.md discovery #4: these don't
update live), `*Total`, `*Close` (closing counts) — all five states per
tender type. `OpenedEmployeeID`/`EditedEmployeeID`/`ClosedEmployeeID`
separately tracked.

## Items / inventory

### Item — the catalog
67 columns. Beyond what's already documented (`Cost`/`LastCost`/
`LastPurchasePrice`, `Stock`, `QtyPerParcel`, `LastSold` — see discoveries
#1/#2/#12): `SerialNumberTracking` (bool — confirms serial tracking is a
per-item opt-in, matching `ReceiptEntry.SerialNumber`), `PeremptionWarningDays`
+ `LastStockTaking` (see the correction to discovery #6 in CLAUDE.md —
a real if partial expiry-warning hook), `LoyaltyRate`/`LoyaltyType`
(per-item loyalty configuration), `ItemSale`/`SaleDiscountType`/
`SaleDiscountValue`/`SaleStartDate`/`SaleEndDate` (a real time-boxed
promotional-price mechanism, separate from `PriceA`-`PriceD` price
levels), 10 extra barcode slots (`BarCodeEx01`-`10`, beyond the primary
`BarCode` — multi-barcode-per-product support), `PromptQty`/`PromptPrice`/
`PromptDiscount` (per-item POS-behavior flags — e.g. "always ask the
cashier to confirm quantity/price/discount for this item"), `BinLocation`
(warehouse/shelf location).

### ItemFamily, UnitOfMeasure, ItemAdjustment, ItemNote, ShortcutItem, ScaleDevice/ScaleDeviceItem
Category tree, unit-of-measure lookup, manual stock adjustments (the
table `shrinkage_events()`-adjacent logic already reads), free-text item
notes, the 10-slot POS hotkey grid, and integrated scale-device/PLU
config (barcode-scale integration, `AclasSDK.dll`/`rtslabelscale.dll`'s
data-side counterpart).

## Purchasing / suppliers

### Purchase — see CLAUDE.md discoveries #3/#11/#12 for the
payment-vs-purchase split already built on this table's `PurchaseEntry`
child rows (not re-derived here; `PurchaseEntry` itself isn't in
`BlankDB`'s own table list — it may be a subordinate structure of
`Purchase` accessed differently, worth checking directly next time).
Same `OldAccount`/`NewAccount` supplier-balance-snapshot pattern as
`Receipt`, plus a full tender breakdown mirroring `Receipt`'s
(`Cash`/`Cheque`/`Transfer`/`Other`/`CreditAccount`) — supplier payments
apparently support the same multi-tender split customer sales do.

### Supplier, SupplierItem
See CLAUDE.md discovery #11 (`TotalPurchased` is a trap — small count,
not a money figure) and discovery #12 (`SupplierItem` never links
`ItemID = -2` payment lines to a supplier). Not yet checked: `LastPurchase`
(datetime) — may have the same staleness risk as `Item.LastSold`
(discovery #1) if it's a POS-maintained field rather than recomputed;
worth the same "recompute from source rows" skepticism before trusting it.

## Customers

### Customer, CustomerFamily
Beyond the already-documented `InitialAccount`/`Account`/`AllowAccount`
receivables fields: **`RC`, `AI`, `NIF`, `NIS`, `CB`** — these read as
Algerian business/tax registration identifiers (Registre de Commerce,
Article d'Imposition, Numéro d'Identification Fiscale, Numéro
d'Identification Statistique, and a bank-account field). **This is
concrete evidence for the LLM council's flagged blind spot** ("nobody
checked whether R.Lynx's design choices reflect real fiscal/tax
requirements") — a from-scratch replacement needs these same fields for
any B2B customer, not as a nice-to-have but likely as a real compliance
requirement for Algerian retail. `LoyaltyAccount`/`AllowLoyaltyAccount`
confirm the loyalty-points system is customer-side too, not just
`Item`-side. `TotalVisits`/`LastVisit` — another POS-maintained
aggregate; apply the same staleness skepticism as `Item.LastSold`
before trusting it without checking.

## Pricing

### PriceLevel, PricingUpdate / PricingUpdateEntry
Multi-price-level system (`PriceA`-`PriceD` on `Item`, keyed by
`PriceLevel`/`Customer.PriceLevelID`). `PricingUpdateEntry` is the exact
table behind CLAUDE.md's "weighted-average cost" correction session
(`Item.Cost` getting overwritten by manual pricing edits, not just
purchases) — see that section for the full story.

## Stock operations

### StockTake / StockTakeEntry
See the correction to CLAUDE.md discovery #5 — `StockTakeEntry` (per-
product counted/expected/cost) now confirmed to exist in the current
schema. `MakeInactive` (bool) on the entry — a stocktake can apparently
deactivate an item directly, worth knowing if a replacement needs the
same "count found zero, and mark it inactive" one-step action.

### StockTransfer / StockTransferEntry, TransferReason
Inter-location/inter-register stock movement with a reason code — not
documented anywhere in pos-tool previously; relevant if a future
replacement needs to model transfers between the 3 stores or between
registers within one store.

## Cash / till / staff pay

### Charge, ChargeType, StoreSafeIn, EmployeeSalary
Generic outgoing-payment ("Charge") and cash-safe-deposit
(`StoreSafeIn`) tables, plus a full employee-salary-payment table with
the same multi-tender shape as `Receipt`/`Purchase`. CLAUDE.md discovery
#12 already checked `StoreSafeIn`/`StoreSafeOut`/`Charge` for supplier
payments specifically and found them empty/irrelevant *for that
question* — that doesn't mean these tables are unused generally, just
not the home of supplier payments (which turned out to live in
`PurchaseEntry` instead, `ItemID = -2`).

### CashCalc (lives in `POSParameter`, not `BlankDB`)
`ID, Coin (money), ACount (int)` — denomination-based till-counting
helper (count how many of each note/coin), local to the register, not
store data.

## Employees / security

### Employee
188 columns, confirmed to be almost entirely permission flags: two master
switches (`PosPermission`, `ManPermission`) plus ~150 granular `epMan*`/
`epPos*` booleans, one per specific action (e.g.
`epManCustomerDelete`, `epPosTransCreditAccount`,
`epPosReceiptGenerateInvoice`). This is a flat-column ACL, not a
normalized permissions table — confirms the council's read that R.Lynx's
reporting/admin layer wasn't built with much structure, even though the
*coverage* of what's permission-gated is actually extensive (worth
mining this exact list as a checklist of "everything a real POS needs a
permission toggle for," regardless of what's wrong with how it's stored).
`LastEmployeeLog` (`MANEmployeeID`, `POSEmployeeID`) — looks like it
tracks the last logged-in user per app (Manager vs POS), not a real
audit log (no timestamp).

## System / config / lookups

- **Store** — one row per store: name, address, logo, three free-text
  "Info" fields plus three more specific to invoices (`InfoInvoice1-3`).
- **Version** — `AName, Version, VType, ComputerName, Author` — looks
  like it records which machine/version last touched this database file.
- **NumberSequence** — one row holding the *current* counter and a
  format-string for every document type (`SaleNo`, `QuoteNo`, `InvoiceNo`,
  `FakeInvoiceNo`, `PurchaseNo`, `StockTakeNo`, etc.), reset by
  `CurrentYear`. Confirms real sequential numbering per document type —
  relevant to any fiscal/tax-compliance requirement around sequential
  receipt numbers.
- **Register, Report** — register name lookup; a `Report` table that
  looks like stored report *definitions* (`ReportType`, `ReportText`),
  not report output.
- **CustomFields, AccessDenied, HomeMessages** — generic extensibility
  slots, a static permission-denied message lookup (not an audit log —
  no timestamp/employee columns), and a home-screen message-of-the-day
  table.
- **Days, Months, Semesters, Trimesters, Houres, Temp** — calendar-label
  lookups and a scratch table; not interesting beyond naming.

## Local-machine config databases (NOT store data — separate files)

These three sit next to the executables, not in the store's `.dblx`:

- **`DBConnect`**: `DBNameSrc, DBNeedCompact, Password, RemoteComputerName,`
  `LastCompactDate, IsNewDB` — the pointer telling `RLPOS.exe` which real
  store database to open. See CLAUDE.md's forensics section for why this
  matters (never point a second instance's copy at a live store's path).
- **`POSParameter`**: 40 columns of per-register hardware/behavior config
  — cash drawer, customer pole display, receipt templates and printer
  names per document type, sound effects, shortcut-grid toggle. Real risk
  flagged in CLAUDE.md: store #1's real `AutoOpenCashDrawer` is `True`.
- **`Task`/`Pad`**: an in-app sticky-note/task-list feature, unrelated to
  sales data — `PadIndex/Index/TaskName/TaskText/ImageIndex` and
  `Index/PadText`.

## Open questions for a future session (needs a live `.dblx`, not `BlankDB`)

1. `Quote` vs `Receipt.ReceiptType == 1` — which one (or both) does a real
   store actually use for devis/quotes?
2. Does `StockTakeEntry` have real rows in any of the 3 stores' live
   databases, now that it's confirmed to exist in the current schema?
3. Is `Item.PeremptionWarningDays` ever non-zero/non-null anywhere?
4. Does `Receipt.OldAccount`/`NewAccount` (and the same on `Purchase`)
   actually track reliably enough to trust for point-in-time balance,
   the same reliability bar CLAUDE.md discovery #2 already applied to
   `PurchaseEntry.NewStock`?
5. Is the loyalty-points system (`Customer.LoyaltyAccount`,
   `Receipt.IsLoyaltyProgram`/`TotalLoyaltyAmount`) actually used by this
   client, or dormant schema?
6. Where does `PurchaseEntry` (referenced throughout CLAUDE.md's AVCO/
   cost-history work) actually live? It wasn't among `BlankDB`'s own
   top-level table list pulled this session — worth double-checking
   whether it's a real separate table missed in this pass, or something
   this pass's `Jet4Reader` walk genuinely didn't surface.
