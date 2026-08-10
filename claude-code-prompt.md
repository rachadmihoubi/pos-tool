# Claude Code Build Prompt — Live POS Analytics & Diagnostics Tool

> Paste everything below the line into Claude Code, in an empty folder.
> Before you paste: put the real folder path of your POS database where it says `<<<DB_FOLDER>>>`.

---

## ROLE

You are building a production tool for a real business owner who is not a developer. He will run this every day. It must be reliable, must never touch his live data, and must explain itself in plain language. Do not build a demo. Do not leave TODOs. Anything you cannot verify against real data, you flag — you never invent a number.

## THE BUSINESS (context — use this to judge what matters)

Wholesale makeup and cosmetics distributor in Algiers, Algeria. Sells to small retail cosmetics shops across the country. Buys from importers and local manufacturers. Physical store inside a competitive market with many similar wholesalers next door. Owner-operated: he buys, sells, prices, posts on social media, and handles the money himself. Currency is DZD. Sales cycle is walk-in — retailers arrive, he shows the goods, they buy.

What he actually needs to know, in priority order:
1. Which customers are worth chasing, and which are quietly leaving
2. Where his cash is stuck (dead stock, unpaid customer accounts)
3. Which products make money and which are silently losing it
4. What is about to run out that actually sells
5. Whether this month is better or worse than the last, and why

## THE DATA SOURCE

His POS software stores everything in a **Microsoft Access Jet 4 database** with a `.dblx` extension. Path:

```
<<<DB_FOLDER>>>
```

**Hard rules on this file:**
- Open it **read-only, always**. Never write to it. Never open the live file directly — Access holds locks. Copy it to a temp working directory first, then parse the copy.
- Do not require the user to install Microsoft Access, ODBC drivers, or mdbtools. Assume nothing is installed except Python.
- Do not require an internet connection for the core tool to work.

### Parsing it — this has already been solved, use this spec

Write a **pure-Python Jet 4 reader**. Do not waste time searching for a library; the working spec is below and it has been verified against this exact file.

- Page size is 4096. Byte 0 of each page is the page type: `0x00` db header, `0x01` data page, `0x02` table definition (TDEF), `0x03`/`0x04` index, `0x05` usage bitmap.
- The file is **not encrypted**. Verify this by checking page 1 and page 2 have sane type bytes.
- The system catalog `MSysObjects` has its TDEF on **page 2**. Parse it to get every table's name, id (= its TDEF page number), type and flags. Table objects are `Type & 0x7F == 1`. Skip names starting with `MSys` or `~`, and skip objects with flag bit `0x80000000`.
- Jet4 TDEF layout offsets: `num_rows` at 16 (uint32), `num_var_cols` at 43 (uint16), `num_cols` at 45 (uint16), `num_idxs` at 47 (uint32), `num_real_idxs` at 51 (uint32). Column definitions start at offset `63 + num_real_idxs * 12`.
- Each column definition is **25 bytes**: type at +0, `col_num` at +5, `var_col_num` at +7 (uint16), `row_col_num` at +9, scale at +11, precision at +12, flags at +15 (bit `0x01` = fixed-length), `fixed_offset` at +21 (uint16), `col_size` at +23 (uint16).
- Column **names** follow the column definitions, in the same order: a uint16 byte-length, then that many bytes of UTF-16LE.
- A TDEF can span multiple pages. Next page pointer is uint32 at offset 4; continuation pages contribute their bytes from offset 8 onward. Total definition length is the uint32 at offset 8 of the first page.
- Column type codes: `1` bool, `2` byte, `3` int16, `4` int32, `5` money (int64 / 10000), `6` float32, `7` float64, `8` datetime (float64 = days since 1899-12-30), `9` binary, `10` text, `11` OLE, `12` memo, `13` GUID, `15` numeric.
- To find a table's **data pages**, scan every page of type `0x01` and read the owning TDEF page number as uint32 at offset 4. Match it to the table's TDEF page. (Simpler and more robust than walking the usage map.)
- On a data page: row count is uint16 at `0x0c`; the row-offset array is uint16 values starting at `0x0e`. Offset flags: `0x8000` = deleted, `0x4000` = overflow pointer — skip both. Mask the offset with `0x1FFF`.
- **Row end — this is the part that breaks naive parsers.** The end of a row is *the next-higher row offset on that page, minus 1* (or 4095 if none is higher). Do **not** assume rows are stored in descending offset order and use `row[i-1]` as the boundary — that is wrong on this file and silently corrupts variable-length fields, which is where every table name and product name lives.
- Row layout: uint16 column count at the row start, then fixed-length fields at `row_start + 2 + fixed_offset`. The last `ceil(row_cols/8)` bytes are the null bitmap — a **set** bit means NOT null, indexed by `col_num` (0-based). Immediately before the bitmap: a uint16 count of variable columns in this row, then that many + 1 uint16 offsets read at `row_end - bitmask_sz - 3 - i*2`. Sort/orient them ascending; consecutive pairs bound each variable field, and the last one is end-of-data.
- **A variable-length column is located by its `var_col_num`, not by its position in the column list.** Getting this wrong returns empty strings for everything.
- Boolean columns carry their value in the null bitmap: bit clear = TRUE, bit set = FALSE.
- **Text decoding.** Jet4 text is UTF-16LE. If the value starts with `FF FE`, the rest is "unicode compressed": strip the prefix, then walk the bytes — a `0x00` byte **toggles** between compressed mode (1 byte per char, pad with `0x00`) and uncompressed mode (2 bytes per char). Do not treat `0x00` as a terminator. This matters: product names mix Latin brand names with Arabic, and a wrong toggle turns Arabic into CJK garbage. Verify by checking that product names render as readable Arabic.
- Memo/OLE fields: 12-byte header — uint32 where the low 24 bits are the length and the high byte holds flags; flag `0x80` means the data is inline right after the header, otherwise the next uint32 is a pointer (`page = ptr >> 8`, `row = ptr & 0xFF`) into LVAL pages. Handle inline first; chase pointers as a second pass.

## THE SCHEMA THAT MATTERS

Around 49 tables. These are the ones with real data:

| Table | Rows (as of last check) | What it is |
|---|---|---|
| `ReceiptEntry` | 39,736 | Sale line items |
| `Receipt` | 7,858 | Sale headers / tickets |
| `PurchaseEntry` | 2,846 | Supplier purchase lines |
| `PricingUpdateEntry` | 2,284 | Price change history |
| `SupplierItem` | 1,647 | Supplier↔product links |
| `ItemAdjustment` | 1,614 | Stock adjustments |
| `Item` | 1,570 | Product catalogue |
| `Customer` | 662 | Customers |
| `Supplier` | 42 | Suppliers |
| `ItemFamily` | 22 | Product categories |

Key columns:
- `Receipt`: `ID`, `CustomerID`, `Time`, `Total`, `TotalCost`, `Margin`, `ReceiptNo`, `Cash`, `Cheque`, `Transfer`, `CreditAccount`, `ReceiptType`
- `ReceiptEntry`: `ID`, `ReceiptID`, `ItemID`, `ItemName`, `Qty`, `Price`, `Cost` (unit cost), `Amount`, `Discount`, `TotalItemMargin`
- `Item`: `ID`, `ItemNo`, `ItemName`, `ItemFamilyID`, `Stock`, `StockAlert`, `Cost`, `Price`, `LastSold`, `LastPurchased`, `Inactive`, `QtyPerParcel`
- `Customer`: `ID`, `CustomerNo`, `CustomerName`, `Phone`, `City`, `Account` (outstanding balance owed to the business), `LastVisit`, `TotalVisits`, `AllowAccount`
- `Supplier`: `ID`, `SupplierName`, `Phone`, `Account`, `TotalPurchased`, `LastPurchase`
- `PurchaseEntry`: `PurchaseID`, `ItemID`, `Qty`, `Price`, `Cost`, `NewCost`, `NewStock`

## DATA QUIRKS — GET THESE RIGHT OR EVERY NUMBER IS WRONG

These are real defects in how the POS records data. Handle them explicitly, and surface them in the tool as a "data quality" panel so the owner can see the impact.

1. **`ReceiptEntry` rows with `ItemID <= 0` are not sales.** They are almost entirely `"Paiement de règlement"` — customers paying down their account balance, recorded as if they were merchandise lines. There are ~30.4M DZD of them. Excluding them is the difference between a true and a fake revenue figure. Report them separately as *collections*, never as revenue.
2. **`Receipt.TotalCost` is unreliable.** It does not reconcile with the line items. Compute cost of goods yourself as `Qty * Cost` per line, and gross profit as `Amount - (Qty * Cost)`. Cross-check against `ReceiptEntry.TotalItemMargin`, which does reconcile.
3. **Negative `Qty` lines are returns** (~141 of them). Keep them in — they should reduce revenue and margin — but count them separately in a returns report.
4. **Some items have zero or missing `Cost`** (~671 lines, ~520k DZD). Their margin is unknowable, not 100%. Exclude them from margin-percentage calculations and list them in the data quality panel as "cost missing — margin unmeasurable".
5. **Negative `Stock` values exist.** These are genuine POS errors (sold more than recorded). Flag them, don't silently clamp to zero.
6. Customer `ID = 1` is `"Client divers"` — anonymous walk-in, not a real customer. Exclude from customer-level analysis, include in revenue.

## WHAT TO BUILD

A local desktop tool. Python. Nothing hosted, nothing that sends his data anywhere.

**Architecture:**
- `poslib/jet4.py` — the pure-Python Access reader described above
- `poslib/etl.py` — copy DB to temp → parse all tables → load into a local **SQLite** cache (`cache.db`) with proper types and indexes. Store a hash + mtime of the source so re-parsing is skipped when nothing changed.
- `poslib/metrics.py` — every business calculation, one function per metric, each returning a dataframe. No calculation logic anywhere else in the codebase.
- `poslib/diagnostics.py` — the rules engine that turns metrics into findings (see below)
- `watcher.py` — watches the DB folder with `watchdog`; on change, waits for the file to stop growing (the POS may still be writing), then re-runs the ETL and refreshes the cache
- `app.py` — local web dashboard (FastAPI or Flask + Chart.js, served on `localhost`). Auto-refreshes when the cache updates.
- `export.py` — one command produces a formatted multi-sheet Excel workbook of every report

**Language — the tool is trilingual:** English, French, and Arabic. A language switcher in the header, persisted between sessions. All interface labels, chart axes, diagnostic findings, Excel sheet names and column headers, and the daily digest must be fully translated — no half-translated screens. Keep every string in `locales/en.json`, `locales/fr.json`, `locales/ar.json`; never hardcode a user-facing string in code. Arabic must render **right-to-left**: flip the layout direction, mirror the charts, and use a font that renders Arabic correctly (Cairo, Tajawal, or Noto Sans Arabic). Product names in the database are already a mix of Latin brand names and Arabic — they display as stored, untranslated, and must render correctly in all three modes.

**Dashboard pages** — the app opens on **Today / Live** by default:

1. **Today / Live** *(default landing page)* — sales so far today vs the same weekday's average, tickets today, top items today, cash vs credit split, running gross margin today, comparison to yesterday and to the same day last week, and the last refresh timestamp. This is the screen he looks at most; make it fast, big-number, readable from across the room, and auto-updating without a manual refresh.
2. **Trend** — monthly revenue, gross profit, margin %, ticket count, average basket, active customers. Line + bar charts. Month-over-month and year-over-year deltas.
3. **Customers** — full list sortable by revenue; RFM-style segmentation (champions / loyal / at risk / lapsed / one-time); a **call list** of lapsed customers (no purchase in 90+ days, 2+ prior visits) sorted by historic revenue, with phone numbers, ready to print
4. **Money owed** — every customer with a non-zero `Account` balance, sorted descending, with days since last purchase and a concentration warning when one customer holds a large share of total receivables
5. **Inventory** — total stock value at cost; dead stock (in stock, 120+ days without a sale); stockout risk (sold in the last 90 days, less than 0.75 months of cover at current run rate); overstock (more than 12 months of cover); negative stock; ABC classification by revenue contribution
6. **Products & margin** — margin % by product and by family; high-revenue/low-margin list; products selling below cost; price-vs-cost drift over time using `PricingUpdateEntry`
7. **Suppliers** — purchase concentration, cost trend per supplier, lead-time proxy from `LastPurchase` gaps
8. **Diagnostics** — see next section
9. **Data quality** — every quirk above, quantified, so he knows how much to trust each number

**Charts:** monthly revenue+margin combo, margin % trend line, cumulative revenue by customer (Pareto curve), stock value split into healthy vs slow vs dead, revenue by family, weekday and hour-of-day heatmap of sales. Clean, readable, no chartjunk. Label axes in the interface language.

## THE DIAGNOSTICS ENGINE — THIS IS THE POINT OF THE TOOL

A rules engine that produces ranked findings. Each finding must have: a **severity**, a **plain-language statement**, the **number behind it**, the **money at stake**, and a **specific action**. No generic advice. No "consider optimizing your inventory."

Write it as declarative rules in one file so new ones can be added easily. Rules to implement at minimum:

- Gross margin below a healthy threshold for the category, or trending down over 3+ months
- Revenue growing while gross profit is flat or falling (discounting or cost creep)
- Receivables concentration: any single customer above 25% of total money owed
- Any customer with a large balance who hasn't purchased in 60+ days (collection risk)
- Any customer generating high revenue at near-zero or negative margin (they are being served at cost)
- Working capital ratio: (stock value + receivables) vs trailing 12-month gross profit — how hard his money is working
- Dead stock above a threshold share of total stock value
- Fast-moving items about to stock out, ranked by lost revenue per week if they go to zero
- Products where selling price hasn't moved but cost has risen (margin silently eroding)
- Customer churn rate month over month, and revenue at risk from the lapsed cohort
- Concentration risk: share of revenue from the top 10 customers
- Seasonality: months that consistently underperform, so he can plan purchasing

For each finding also compute **"what happens if you fix it"** — e.g. collecting X% of overdue receivables frees N DZD; clearing dead stock at a Y% discount recovers N DZD; raising margin on the bottom-decile products by 2 points adds N DZD per year. Show the arithmetic.

Group the output into **What's working** and **What's failing**, each ranked by money impact, not by severity label.

## VERIFICATION — DO NOT SKIP THIS

The extraction has already been validated once. Your build must reproduce these figures within rounding. If it doesn't, your parser is wrong — fix it before moving on.

| Check | Expected |
|---|---|
| `Receipt` rows | 7,858 |
| `ReceiptEntry` rows | 39,736 |
| `Item` rows | 1,570 |
| `Customer` rows | 662 |
| Date range | 2024-09-24 → present |
| Revenue excluding `ItemID <= 0` (all time) | 266,299,322 DZD |
| Gross profit (all time, line-level) | 26,686,300 DZD |
| Trailing-12-month revenue | ~167,589,951 DZD |
| Trailing-12-month gross profit | ~14,635,724 DZD |
| Stock value at cost | 59,168,540 DZD |
| Dead stock value (120+ days) | 12,495,043 DZD |
| Total customer receivables | 18,035,898 DZD |
| "Paiement de règlement" pseudo-sales | 30,420,753 DZD |

(The trailing-12-month and dead-stock figures will drift as new data arrives — they were computed as of the last extraction. The all-time and row-count figures should match on the same source file.)

Write unit tests for the parser (a known row decoded correctly, Arabic text rendering correctly, null handling) and integration tests asserting the totals above.

## HOW IT MUST RUN

- One command to set up: a `setup.bat` (this is Windows) that creates a virtual environment and installs dependencies.
- One command to start: a `start.bat` that launches the watcher and the dashboard and opens the browser.
- A Windows Task Scheduler entry, generated for him, so it starts with the machine.
- A daily digest at a configurable hour: yesterday's numbers, changes since the day before, new diagnostic findings, and urgent alerts (stockouts imminent, overdue balances, a top customer going quiet). One page, written in the selected language.

  Build the digest as a **content generator plus pluggable delivery channels**, so a channel can fail without breaking the others. Three channels, all enabled:
  1. **File** — always on, cannot be disabled. Writes a dated HTML and PDF to a local `digests/` folder. This is the fallback that always works.
  2. **Email** — SMTP, credentials in a `.env` file that is gitignored, never in `config.yaml`. Support Gmail app passwords. Send the HTML inline plus the Excel export as an attachment. Retry on failure, log, and never crash the app.
  3. **WhatsApp** — implement, but read this first and tell him plainly what it costs before you build it: the official WhatsApp Business Cloud API is the only sanctioned route, it requires a Meta Business account, a verified phone number, template-message approval for anything sent outside a 24-hour customer-initiated window, and it bills per conversation. Unofficial libraries that drive WhatsApp Web will get his number banned — do not use them. Build the official Cloud API integration behind an interface, and ship a **Telegram bot** channel alongside it as a free, five-minute-setup alternative he can use immediately while the WhatsApp approval goes through. Let him enable either or both.

  Each channel is a class implementing `send(digest, language)`. Adding a fourth later should mean one new file.

- `config.yaml` for: DB path, currency, thresholds (dead-stock days, cover months, lapsed days), default interface language, digest time, and which delivery channels are enabled. Secrets never go here — they go in `.env`.

## HOW TO WORK

1. Language (English + French + Arabic), digest delivery (file + email + WhatsApp/Telegram) and the default landing page (Today / Live) are already decided above — don't re-ask. Ask only for what is genuinely blocking: the exact DB file path, and SMTP details when you reach the email channel. Ask once, in one message, then build.
2. Build in this order, and get each stage working against the real file before moving on: parser → SQLite cache → metrics → dashboard (Today page first, then the rest) → diagnostics → watcher → i18n across all three languages → export → digest channels → scheduling. Wire the i18n layer in from the first screen you build; retrofitting translations into a finished UI is twice the work.
3. After each stage, print the verification numbers and compare them to the table above.
4. Keep every business rule and threshold in `config.yaml` or `metrics.py`. Never bury a magic number in a view.
5. Comment in plain language. He will read this code eventually.
6. When a metric is ambiguous, don't guess — put it in the data quality panel and tell him what you assumed.
7. Do not use `pyodbc`, `mdbtools`, or anything requiring an Access install.
8. The tool never writes to the source database. Enforce it in code — open the source with read-only flags, and copy before parsing.

Start now. Ask your blocking questions, then build the parser and prove it against the verification table.
