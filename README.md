# Shop Analysis

A tool that reads your POS database and tells you, in plain language, what is
working, what is losing you money, and what to do about it.

It runs entirely on this computer. Nothing is sent anywhere. **It never writes
to your POS database** — it copies it somewhere temporary and reads the copy.

Available in **English, French and Arabic**.

---

## Getting started

**Once, the first time:**

1. Double-click **`setup.bat`** and wait. It needs an internet connection for
   this step only.
2. Open **`config.yaml`** in Notepad and check this points at your real POS
   database:

   ```yaml
   database:
     path: "C:/Users/Quick Tech/Desktop/pos-tool/Base de données4.dblx"
   ```

**Every day:**

- Double-click **`start.bat`**. Your browser opens on the Today screen.
- Leave the black window open. Closing it stops the tool.

**To make it start by itself with the computer:**

- Right-click **`install-startup.bat`** → *Run as administrator*.
- To undo that later: **`uninstall-startup.bat`**.

---

## The screens

| Screen | What it answers |
|---|---|
| **Today** | How is today going, against yesterday and against a usual day of this weekday |
| **Trend** | Is the business getting better or worse, and why |
| **Customers** | Who is worth chasing, who is quietly leaving, and a printable call list |
| **Money owed** | Who owes you what, and who has gone quiet while owing it |
| **Stock** | Where your money is stuck, and what is about to run out |
| **Products & margin** | What actually makes money, and what quietly loses it |
| **Suppliers** | Who you depend on, and how often you reorder |
| **What to fix** | Every problem found, ranked by money, with what to do about each |
| **Data quality** | How much to trust each number on the other screens |

The language switcher is in the top right. Your choice is remembered.

---

## The daily digest

Once a day (8pm by default, set in `config.yaml`) the tool writes a one-page
summary of yesterday and sends it however you have asked.

**Four ways to receive it. They are independent — if one fails, the others
still work.**

### 1. Saved to a file — always on, cannot be switched off

Writes an HTML page and a PDF into the `digests` folder. This is the fallback
that works with no internet, no password and no third party.

### 2. Email

In `config.yaml`:

```yaml
digest:
  channels:
    email:
      enabled: true
      from_address: "you@gmail.com"
      to_addresses: ["you@gmail.com"]
```

The password goes in the `.env` file, **never** in `config.yaml`. For Gmail
you need an *app password*, not your normal one:

1. <https://myaccount.google.com/security> → turn on **2-Step Verification**
2. <https://myaccount.google.com/apppasswords> → create one called "Shop tool"
3. Paste the 16 letters (no spaces) into `.env` as `SMTP_PASSWORD=`

The email includes the digest and the full Excel report as an attachment.

### 3. Telegram — free, five minutes, no approval

This is the recommended messaging channel.

1. In Telegram, message **@BotFather** and send `/newbot`. It gives you a token.
2. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=`
3. Send your new bot any message, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` and find
   `"chat":{"id":123456789`. Put that number in `.env` as `TELEGRAM_CHAT_ID=`
4. Set `enabled: true` under `telegram` in `config.yaml`

No cost, no verification, no waiting. Arabic displays correctly.

### 4. WhatsApp — read this before asking for it

**What it costs and what it needs, plainly:**

- A **Meta Business account** with your business **verified**.
- A **phone number dedicated to the API**. Once connected, that number can no
  longer be used in the normal WhatsApp or WhatsApp Business app. It cannot be
  your everyday number.
- **Template approval.** The digest is a message *you* start, not a reply.
  Outside the 24-hour window after a customer messages you, only templates that
  Meta has approved may be sent. Approval takes anywhere from minutes to
  several days, and templates are rejected for wording Meta dislikes.
- **Money.** Meta bills per conversation. The free allowance is small.
- Because a template's wording is fixed once approved, the WhatsApp digest is a
  **short summary** — the headline numbers and the single most urgent finding.
  The full digest goes to file and email.

**What this tool will not do:** there are Python libraries that automate
WhatsApp Web by driving a browser. They breach WhatsApp's terms and are a
reliable way to get a business number **permanently banned**. None is used
here, and a test in the suite fails if one is ever added.

**Recommendation:** use Telegram. It does the same job, today, for nothing.
Both can be switched on at once if you later get WhatsApp approved.

---

## Settings

Everything you might want to change is in **`config.yaml`**, with a plain
explanation above each one. The ones people change most:

```yaml
interface:
  default_language: "fr"     # en, fr or ar

thresholds:
  inventory:
    dead_stock_days: 120     # no sale for this long = dead stock
    stockout_cover_months: 0.75
  customers:
    lapsed_days: 90          # no purchase for this long = on the call list
    collection_risk_days: 60
  margin:
    healthy_gross_margin: 0.10   # 10%

digest:
  hour: 20                   # when the digest goes out
```

Passwords and tokens go in **`.env`** — never in `config.yaml`. See
`.env.example`.

---

## How the numbers are worked out

Your POS records a few things in ways that give wrong answers if taken at face
value. Each is handled, and each is quantified on the **Data quality** screen so
you can see the size of what is being corrected.

1. **Account payments are recorded as if they were goods.** About
   **30.4M DZD** of "sales" are customers paying down what they owe, mostly
   labelled *Paiement de règlement*. Counted as **collections**, never as
   revenue. Including them would overstate sales by more than a tenth.

2. **The cost written on the ticket is sometimes zero.** Nine tickets — all the
   "DV" series — record no cost at all, understating cost of goods by
   **1.17M DZD**. Cost is therefore always added up from the individual lines,
   never taken from the ticket header.

3. **Returns** (negative quantities) stay in the figures so they correctly
   reduce revenue and profit, and are counted separately.

4. **Some lines have no cost recorded** — 671 lines worth about 521k DZD, mostly
   the *Article divers* catch-all. Their margin is **unknown, not 100%**. They
   are excluded from every margin percentage and their revenue still counts.

5. **Negative stock** is flagged, never quietly set to zero. 74 products are
   affected. It means goods went out without being booked in.

6. **Customer 1, "Client divers"**, is the anonymous walk-in till. Its money
   counts as revenue but it is never treated as a customer.

Two further problems were found while building this, and are handled the same
way:

7. **The "last sold" date on products is not always updated.** 60 products carry
   a date older than sales actually recorded against them — one by 370 days. The
   date of the most recent ticket is used instead. Trusting the product field
   would have wrongly condemned **584,340 DZD** of live stock as dead.

8. **Purchase totals do not reconcile.** The purchase lines add up to roughly
   twice the cost of everything ever sold plus everything still on the shelf.
   Purchase amounts are therefore used only to compare suppliers against each
   other, never as an amount of money spent. This is worth fixing in the POS.

---

## Proof the database is being read correctly

The bottom of the **Data quality** screen recomputes the figures that were
checked by hand when this was built. The all-time totals should only ever grow,
by exactly the value of new sales.

| Check | Verified at build |
|---|---|
| `Receipt` rows | 7,858 |
| `ReceiptEntry` rows | 39,736 |
| `Item` / `Customer` rows | 1,570 / 662 |
| Revenue, all time | 266,299,322 DZD |
| Gross profit, all time | 26,686,300 DZD |
| Account payments | 30,420,753 DZD |
| Stock at cost | 59,168,540 DZD |
| Money owed | 18,035,898 DZD |

If one of these ever **falls** or **jumps**, something is wrong and it should be
looked at. `python -m pytest tests` checks all of them.

---

## Running things by hand

```
.venv\Scripts\python.exe -m poslib.etl --force     read the database now
.venv\Scripts\python.exe export.py --lang ar       build the Excel report
.venv\Scripts\python.exe -m poslib.digest --dry-run   print the digest
.venv\Scripts\python.exe -m poslib.digest          send the digest now
.venv\Scripts\python.exe watcher.py --once         refresh once and stop
.venv\Scripts\python.exe -m pytest tests -q        check everything still works
```

---

## If something goes wrong

**"Python is not installed"** — install it from python.org and tick *Add Python
to PATH* on the first screen of the installer.

**"Cannot find the POS database"** — the path in `config.yaml` is wrong. Find
your `.dblx` file, right-click it, *Copy as path*, and paste it in. Forward
slashes work fine.

**The dashboard shows old numbers** — press *Refresh now*. If that does not help,
the watcher may have stopped; restart with `start.bat`.

**No email arrived** — check `logs\pos-tool.log`. The most common cause is using
a normal Google password instead of an app password.

**The tool will not start** — delete the `.venv` folder and run `setup.bat` again.

Nothing you can do here can damage your POS data. The tool only ever opens it
read-only and always works on a copy.

---

## How it is put together

```
poslib/jet4.py         reads the Access file directly, byte by byte
poslib/etl.py          copies the database, parses it, fills cache.db
poslib/metrics.py      every business calculation, and nothing else
poslib/diagnostics.py  the rules that turn numbers into things to do
poslib/charts.py       chart geometry (plain SVG, no libraries)
poslib/i18n.py         the three languages
poslib/digest.py       the daily summary
poslib/channels/       how the summary is delivered - one file each
locales/               every word on screen: en.json, fr.json, ar.json
app.py                 the dashboard
watcher.py             notices new sales, sends the digest
export.py              the Excel report
tests/                 84 checks, including the totals above
```

Two rules worth knowing if you ever change anything:

- **Every calculation lives in `metrics.py`.** If a number looks wrong, that is
  the only file to read.
- **No text a person reads is written in the code.** It all comes from the three
  files in `locales/`, and a test fails if they ever drift apart.

There is no Microsoft Access, ODBC driver or mdbtools on this machine. The
database reader is written from scratch in `poslib/jet4.py`, which is why the
tool needs nothing installed but Python.

---

## File structure

# Python
__pycache__/
*.pyc
*.pyo
*.pyd
*.db
.cache
cache.db

# Virtual environment
.venv/
venv/

# OS
Thumbs.db
Desktop.ini

# IDE
.vscode/
*.code-workspace
