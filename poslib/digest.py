"""
digest.py - the one-page summary of yesterday.

Two separate jobs, deliberately kept apart:

  Digest          works out what to say and writes it in one of three
                  languages, as a web page or as plain text
  channels/       get it to the owner - saved to a file, emailed, sent on
                  Telegram or WhatsApp

Because they are separate, a channel can fail without affecting the content,
and a new way of sending it means one new file and no change here.

WHAT GOES IN IT
---------------
Yesterday's numbers, how they compare with the day before and with a usual
day of that weekday, anything urgent (about to run out, owed money and gone
quiet, a big customer going silent), and the diagnostic findings that are
NEW since the last digest - so the same problem is not shouted about every
single morning.
"""

from __future__ import annotations

import datetime
import html
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .config import Config, get_config
from .diagnostics import Diagnostics
from .etl import ETL
from .i18n import Translator, get_translator, normalise
from .metrics import Metrics

log = logging.getLogger(__name__)

# Remembers which findings have already been reported, so "new since
# yesterday" means something.
STATE_FILE = "digest-state.json"


@dataclass
class Digest:
    """Yesterday, summed up. Renders itself into any of the three languages."""

    for_date: datetime.date
    numbers: dict[str, Any] = field(default_factory=dict)
    comparisons: dict[str, Any] = field(default_factory=dict)
    top_items: list[dict[str, Any]] = field(default_factory=list)
    top_customers: list[dict[str, Any]] = field(default_factory=list)
    urgent: list[dict[str, Any]] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    new_finding_ids: list[str] = field(default_factory=list)
    weekday: int = 0
    generated_at: datetime.datetime = field(default_factory=datetime.datetime.now)
    cfg: Config | None = None

    # Filled in by the file channel once it has written them.
    html_path: Path | None = None
    pdf_path: Path | None = None

    _rendered: dict[str, str] = field(default_factory=dict, repr=False)
    _findings_by_lang: dict[str, list[dict[str, Any]]] = field(default_factory=dict,
                                                               repr=False)

    # -- text -------------------------------------------------------------

    def subject(self, language: str) -> str:
        t = get_translator(language)
        return t.get("digest.subject", date=t.date(self.for_date))

    def _findings_for(self, language: str) -> list[dict[str, Any]]:
        """The findings, rendered into one language, cached per language."""
        if language not in self._findings_by_lang:
            t = get_translator(language)
            self._findings_by_lang[language] = [
                f.render(t) if hasattr(f, "render") else f for f in self.findings
            ]
        return self._findings_by_lang[language]

    def text(self, language: str) -> str:
        """
        A plain-text version - for Telegram, and for mail readers that
        refuse to show HTML.
        """
        t = get_translator(language)
        n, c = self.numbers, self.comparisons
        weekday = t.weekday_name(self.weekday)
        lines: list[str] = []

        lines.append(f"{t.get('digest.title')} — {t.date(self.for_date)}")
        lines.append("")

        if not n.get("tickets"):
            lines.append(t.get("digest.no_sales"))
        else:
            lines.append(f"{t.get('digest.revenue')}: {t.money(n['revenue'])}"
                         f"  ({t.signed_percent(c.get('revenue_vs_day_before'))} "
                         f"{t.get('digest.vs_day_before')})")
            lines.append(f"{t.get('digest.gross_profit')}: {t.money(n['gross_profit'])}"
                         f"  ({t.get('digest.margin')} "
                         f"{t.percent(n['margin_pct']) if n.get('margin_pct') is not None else '—'})")
            lines.append(f"{t.get('digest.tickets')}: {t.number(n['tickets'])}"
                         f"  ({t.get('digest.avg_basket')} {t.money(n['avg_basket'])})")
            if n.get("collections"):
                lines.append(f"{t.get('digest.collections')}: {t.money(n['collections'])}")
            lines.append(f"{t.get('digest.vs_same_weekday', weekday=weekday)}: "
                         f"{t.signed_percent(c.get('revenue_vs_weekday_avg'))}")

        if self.top_items:
            lines.append("")
            lines.append(t.get("digest.top_items") + ":")
            for r in self.top_items[:5]:
                lines.append(f"  • {r['item_name']} — {t.money(r['revenue'])}")

        if self.urgent:
            lines.append("")
            lines.append("— " + t.get("digest.urgent_title") + " —")
            for u in self.urgent:
                lines.append(f"  ! {self._urgent_text(u, t)}")

        rendered = self._findings_for(language)
        new_ones = [f for f in rendered if f["id"] in self.new_finding_ids]
        if new_ones:
            lines.append("")
            lines.append("— " + t.get("digest.new_findings") + " —")
            for f in new_ones[:4]:
                lines.append(f"  • {f['title']}")
                if f.get("money"):
                    lines.append(f"    {t.get('diagnostics.money_at_stake')}: {f['money_label']}")

        # Anything already listed as new is not repeated here.
        top_findings = [f for f in rendered if f["id"] not in self.new_finding_ids][:3]
        if top_findings:
            lines.append("")
            lines.append("— " + t.get("digest.findings_title") + " —")
            for f in top_findings:
                lines.append(f"  {f['title']}")
                lines.append(f"    → {f['action']}")

        lines.append("")
        lines.append(t.get("digest.footer", time=t.datetime(self.generated_at)))
        return "\n".join(lines)

    def _urgent_text(self, u: dict[str, Any], t: Translator) -> str:
        kind = u["kind"]
        if kind == "stockout":
            return t.get("digest.stockout_alert", name=u["name"],
                         days=t.number(u["days"]), weekly=t.money(u["weekly"]))
        if kind == "overdue":
            return t.get("digest.overdue_alert", name=u["name"],
                         amount=t.money(u["amount"]), days=t.number(u["days"]))
        if kind == "quiet":
            return t.get("digest.quiet_customer_alert", name=u["name"],
                         usual=t.number(u["usual"]), days=t.days_ago(u["days"]))
        return ""

    # -- the page ----------------------------------------------------------

    def html(self, language: str) -> str:
        """
        A self-contained web page. All the styling is inside it, so it looks
        the same saved on disk, printed, or opened inside an email.
        """
        if language in self._rendered:
            return self._rendered[language]

        t = get_translator(language)
        e = html.escape
        n, c = self.numbers, self.comparisons
        weekday = t.weekday_name(self.weekday)
        rtl = t.is_rtl
        font = ("'Cairo','Tajawal','Noto Sans Arabic','Segoe UI',Tahoma,sans-serif"
                if rtl else "'Segoe UI',Roboto,Arial,sans-serif")

        def delta_html(value: Any) -> str:
            if value is None:
                return '<span style="color:#8b95a3">—</span>'
            colour = "#14795a" if value > 0.005 else ("#b3261e" if value < -0.005 else "#8b95a3")
            return f'<span style="color:{colour};font-weight:600">{e(t.signed_percent(value))}</span>'

        def tile(label: str, value: str, sub: str = "") -> str:
            return (
                '<td style="padding:14px 16px;background:#fff;border:1px solid #e2e6ec;'
                'border-radius:10px;vertical-align:top">'
                f'<div style="color:#5b6472;font-size:12px;margin-bottom:5px">{label}</div>'
                f'<div style="font-size:27px;font-weight:700;letter-spacing:-.5px">{value}</div>'
                + (f'<div style="color:#8b95a3;font-size:12px;margin-top:5px">{sub}</div>' if sub else "")
                + "</td>"
            )

        parts: list[str] = []
        parts.append(f"""<!doctype html>
<html lang="{language}" dir="{'rtl' if rtl else 'ltr'}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(self.subject(language))}</title>
<style>
  @page {{ size: A4; margin: 14mm; }}
  body {{ font-family: {font}; background:#f4f5f7; color:#16191d;
          margin:0; padding:22px; font-size:15px; line-height:1.5; }}
  .sheet {{ max-width: 860px; margin: 0 auto; }}
  h1 {{ font-size:23px; margin:0 0 3px; }}
  h2 {{ font-size:16px; margin:24px 0 10px; }}
  .sub {{ color:#5b6472; font-size:13.5px; margin:0 0 18px; }}
  table.tiles {{ width:100%; border-spacing:10px; border-collapse:separate; }}
  table.data {{ width:100%; border-collapse:collapse; background:#fff;
                border:1px solid #e2e6ec; border-radius:10px; overflow:hidden; }}
  table.data th {{ background:#f6f7f9; color:#5b6472; font-size:11.5px;
                   text-transform:uppercase; letter-spacing:.3px;
                   padding:8px 11px; text-align:{'right' if rtl else 'left'};
                   border-bottom:1px solid #e2e6ec; }}
  table.data td {{ padding:8px 11px; border-bottom:1px solid #eef0f3; font-size:14px; }}
  table.data td.num {{ text-align:{'left' if rtl else 'right'};
                       font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .card {{ background:#fff; border:1px solid #e2e6ec; border-radius:10px;
           padding:14px 17px; margin-bottom:10px;
           border-{'right' if rtl else 'left'}:5px solid #cbd2dc; }}
  .card.crit {{ border-{'right' if rtl else 'left'}-color:#8f1d17; }}
  .card.high {{ border-{'right' if rtl else 'left'}-color:#b3261e; }}
  .card.med  {{ border-{'right' if rtl else 'left'}-color:#9a6400; }}
  .card h3 {{ margin:0 0 6px; font-size:15.5px; }}
  .card .money {{ float:{'left' if rtl else 'right'}; font-weight:700; color:#b3261e; }}
  .card .act {{ background:#e9f1fb; border-radius:7px; padding:9px 12px;
                margin-top:9px; font-size:14px; }}
  .urgent {{ background:#fdecea; border:1px solid #f3c6c2; color:#8f1d17;
             border-radius:10px; padding:13px 16px; margin-bottom:10px; }}
  .ok {{ background:#e7f5ef; border:1px solid #b9e2d2; color:#0e5b44;
         border-radius:10px; padding:13px 16px; }}
  .foot {{ color:#8b95a3; font-size:12px; margin-top:26px; text-align:center; }}
</style>
</head>
<body><div class="sheet">""")

        parts.append(f"<h1>{e(t.get('digest.title'))}</h1>")
        parts.append(f'<p class="sub">{e(t.date(self.for_date))} · {e(weekday)}</p>')

        if not n.get("tickets"):
            parts.append(f'<div class="urgent">{e(t.get("digest.no_sales"))}</div>')
        else:
            parts.append('<table class="tiles"><tr>')
            parts.append(tile(
                e(t.get("digest.revenue")), e(t.money(n["revenue"])),
                f'{delta_html(c.get("revenue_vs_day_before"))} {e(t.get("digest.vs_day_before"))}'))
            parts.append(tile(
                e(t.get("digest.gross_profit")), e(t.money(n["gross_profit"])),
                f'{e(t.get("digest.margin"))} '
                f'{e(t.percent(n["margin_pct"]) if n.get("margin_pct") is not None else "—")}'))
            parts.append(tile(
                e(t.get("digest.tickets")), e(t.number(n["tickets"])),
                f'{e(t.get("digest.avg_basket"))} {e(t.money(n["avg_basket"]))}'))
            parts.append("</tr><tr>")
            parts.append(tile(
                e(t.get("digest.vs_same_weekday", weekday=weekday)),
                delta_html(c.get("revenue_vs_weekday_avg")),
                e(t.money(c.get("weekday_average") or 0))))
            parts.append(tile(
                e(t.get("digest.collections")), e(t.money(n.get("collections") or 0))))
            parts.append(tile(
                e(t.get("common.units")), e(t.number(n.get("units") or 0))))
            parts.append("</tr></table>")

        # ---- urgent
        parts.append(f"<h2>{e(t.get('digest.urgent_title'))}</h2>")
        if self.urgent:
            for u in self.urgent:
                parts.append(f'<div class="urgent">⚠ {e(self._urgent_text(u, t))}</div>')
        else:
            parts.append(f'<div class="ok">✓ {e(t.get("digest.urgent_none"))}</div>')

        # ---- best sellers
        if self.top_items:
            parts.append(f"<h2>{e(t.get('digest.top_items'))}</h2>")
            parts.append('<table class="data"><tr>'
                         f'<th>{e(t.get("common.product"))}</th>'
                         f'<th>{e(t.get("common.quantity"))}</th>'
                         f'<th>{e(t.get("common.revenue"))}</th></tr>')
            for r in self.top_items[:6]:
                parts.append(f'<tr><td>{e(str(r["item_name"]))}</td>'
                             f'<td class="num">{e(t.number(r["qty"]))}</td>'
                             f'<td class="num">{e(t.money(r["revenue"]))}</td></tr>')
            parts.append("</table>")

        if self.top_customers:
            parts.append(f"<h2>{e(t.get('digest.top_customers'))}</h2>")
            parts.append('<table class="data"><tr>'
                         f'<th>{e(t.get("common.customer"))}</th>'
                         f'<th>{e(t.get("common.revenue"))}</th></tr>')
            for r in self.top_customers[:5]:
                parts.append(f'<tr><td>{e(str(r["customer_name"]))}</td>'
                             f'<td class="num">{e(t.money(r["revenue"]))}</td></tr>')
            parts.append("</table>")

        # ---- findings
        rendered = self._findings_for(language)
        new_ones = [f for f in rendered if f["id"] in self.new_finding_ids]
        if new_ones:
            parts.append(f"<h2>{e(t.get('digest.new_findings'))}</h2>")
            for f in new_ones[:4]:
                parts.append(self._finding_html(f, t, e))

        top = [f for f in rendered if f["id"] not in self.new_finding_ids][:3]
        if top:
            parts.append(f"<h2>{e(t.get('digest.findings_title'))}</h2>")
            for f in top:
                parts.append(self._finding_html(f, t, e))

        parts.append(f'<p class="foot">{e(t.get("digest.footer", time=t.datetime(self.generated_at)))}</p>')
        parts.append("</div></body></html>")

        page = "\n".join(parts)
        self._rendered[language] = page
        return page

    @staticmethod
    def _finding_html(f: dict[str, Any], t: Translator, e) -> str:
        css = {"critical": "crit", "high": "high", "medium": "med"}.get(f["severity"], "")
        money = (f'<span class="money">{e(f["money_label"])}</span>'
                 if f.get("money") else "")
        fix = (f'<div style="color:#14795a;font-weight:600;font-size:13.5px;'
               f'margin-top:7px">→ {e(f["fix"])}</div>' if f.get("fix") else "")
        return (f'<div class="card {css}">{money}<h3>{e(f["title"])}</h3>'
                f'<div style="font-size:14px">{e(f["statement"])}</div>'
                f'<div class="act"><b>{e(t.get("diagnostics.what_to_do"))}:</b> '
                f'{e(f["action"])}</div>{fix}</div>')

    # -- for the WhatsApp template ----------------------------------------

    def template_variables(self, language: str) -> list[str]:
        """
        The values that fill an approved WhatsApp template, in order.

        Kept short and in a fixed order because a template's wording cannot
        be changed once Meta has approved it.
        """
        t = get_translator(language)
        n = self.numbers
        rendered = self._findings_for(language)
        headline = rendered[0]["title"] if rendered else t.get("diagnostics.failing_empty")
        return [
            t.date(self.for_date),
            t.money(n.get("revenue") or 0),
            t.percent(n.get("margin_pct")) if n.get("margin_pct") is not None else "—",
            t.number(n.get("tickets") or 0),
            headline[:180],
        ]

    # -- the Excel attachment ---------------------------------------------

    def excel_path(self, language: str) -> Path | None:
        """Build the Excel report so the email channel can attach it."""
        try:
            from export import build_workbook
            return build_workbook(language=language, cfg=self.cfg)
        except Exception:                                # noqa: BLE001
            log.exception("Could not build the Excel report for the digest")
            return None


# ---------------------------------------------------------------------------
# Working out what to say
# ---------------------------------------------------------------------------

def build_digest(cfg: Config | None = None,
                 for_date: datetime.date | None = None) -> Digest:
    """Gather everything the digest needs to say."""
    cfg = cfg or get_config()
    etl = ETL(cfg)
    conn = etl.connect()

    try:
        m = Metrics(conn, cfg)
        target = for_date or (datetime.date.today() - datetime.timedelta(days=1))

        sales = m.sales
        tickets = m.tickets

        def day(d: datetime.date) -> dict[str, Any]:
            s = sales[sales["ticket_time"].dt.date == d]
            tk = tickets[tickets["ticket_time"].dt.date == d]
            known = s[s["cost_known"]]
            revenue = float(s["amount"].sum())
            count = int(tk["receipt_id"].nunique())
            measurable = float(known["amount"].sum())
            return {
                "revenue": revenue,
                "gross_profit": float(s["gross_profit"].sum()),
                "margin_pct": (float(known["gross_profit"].sum()) / measurable)
                if measurable else None,
                "tickets": count,
                "avg_basket": revenue / count if count else 0.0,
                "units": float(s["qty"].sum()),
                "collections": float(
                    m.collections[m.collections["ticket_time"].dt.date == d]["amount"].sum()),
            }

        numbers = day(target)
        before = day(target - datetime.timedelta(days=1))

        # The same weekday, averaged over the last eight, ignoring closed days.
        weekday = target.weekday()
        history: list[float] = []
        probe = target - datetime.timedelta(days=7)
        first = m.data_range["first"]
        floor = first.date() if first is not None else target
        while len(history) < 8 and probe >= floor:
            v = float(sales[sales["ticket_time"].dt.date == probe]["amount"].sum())
            if v > 0:
                history.append(v)
            probe -= datetime.timedelta(days=7)
        weekday_avg = sum(history) / len(history) if history else 0.0

        def change(now: float, then: float) -> float | None:
            return ((now - then) / then) if then else None

        comparisons = {
            "revenue_vs_day_before": change(numbers["revenue"], before["revenue"]),
            "profit_vs_day_before": change(numbers["gross_profit"], before["gross_profit"]),
            "revenue_vs_weekday_avg": change(numbers["revenue"], weekday_avg),
            "weekday_average": weekday_avg,
            "day_before": before,
        }

        s_day = sales[sales["ticket_time"].dt.date == target]
        top_items = (s_day.groupby(["item_id", "item_name"], as_index=False)
                     .agg(qty=("qty", "sum"), revenue=("amount", "sum"))
                     .sort_values("revenue", ascending=False)
                     .head(6).to_dict("records"))

        walkin = m.walkin_id
        top_cust_df = (s_day[s_day["customer_id"] != walkin]
                       .groupby("customer_id", as_index=False)
                       .agg(revenue=("amount", "sum")))
        top_customers: list[dict[str, Any]] = []
        if not top_cust_df.empty:
            top_customers = (top_cust_df
                             .merge(m.customers[["customer_id", "customer_name"]],
                                    on="customer_id", how="left")
                             .sort_values("revenue", ascending=False)
                             .head(5).to_dict("records"))

        diag = Diagnostics(m, cfg)
        findings = diag.ranked("failing")

        digest = Digest(
            for_date=target,
            numbers=numbers,
            comparisons=comparisons,
            top_items=top_items,
            top_customers=top_customers,
            urgent=_urgent_items(m, cfg),
            findings=findings,
            weekday=weekday,
            cfg=cfg,
        )
        digest.new_finding_ids = _find_new(cfg, [f.id for f in findings])
        return digest
    finally:
        conn.close()


def _urgent_items(m: Metrics, cfg: Config) -> list[dict[str, Any]]:
    """
    The handful of things worth interrupting somebody's evening for.

    Deliberately short. A list of forty urgent things is a list of nothing
    urgent at all.
    """
    out: list[dict[str, Any]] = []

    # About to run out, ranked by what a week without them costs.
    risk = m.stockout_risk()
    if not risk.empty:
        for _, r in risk.head(3).iterrows():
            days = float(r["days_of_cover"])
            if days > 14:
                continue
            out.append({"kind": "stockout", "name": str(r["item_name"]),
                        "days": max(0, round(days)),
                        "weekly": float(r["weekly_revenue"])})

    # Owes money and has gone quiet.
    rec = m.receivables()
    if not rec.empty:
        risky = rec[rec["at_risk"]].sort_values("balance", ascending=False)
        for _, r in risky.head(2).iterrows():
            out.append({"kind": "overdue", "name": str(r["customer_name"]),
                        "amount": float(r["balance"]),
                        "days": int(r["days_since_purchase"] or 0)})

    # A big customer who has gone quiet by their own standards.
    cs = m.customer_summary()
    if not cs.empty:
        for _, r in cs.nlargest(15, "revenue_12m").iterrows():
            visits = float(r["visits"])
            tenure = float(r["tenure_days"] or 0)
            recency = float(r["recency_days"] or 0)
            if visits < 4 or tenure <= 0:
                continue
            usual = tenure / visits
            if recency >= max(usual * 3, 30):
                out.append({"kind": "quiet", "name": str(r["customer_name"]),
                            "usual": round(usual), "days": round(recency)})
                break

    return out[:6]


# How long a finding is still considered "already reported" after the last
# day it showed up. Comparing against only the single previous digest would
# call a finding "new" again the moment it disappears for one day and comes
# back - a rounding blip in the numbers, not a fresh problem.
_RECENTLY_SEEN_DAYS = 7


def _find_new(cfg: Config, current_ids: list[str]) -> list[str]:
    """
    Which findings have not been reported in the last week.

    Repeating the same six problems every single morning trains somebody to
    stop reading. What has just appeared - or come back after a real gap -
    is what deserves attention.
    """
    path = cfg.digest_dir / STATE_FILE
    last_seen: dict[str, str] = {}
    try:
        if path.is_file():
            last_seen = json.loads(path.read_text(encoding="utf-8")).get("last_seen", {})
    except (OSError, ValueError):
        last_seen = {}

    today = datetime.date.today()
    cutoff = today - datetime.timedelta(days=_RECENTLY_SEEN_DAYS)

    def recently_seen(finding_id: str) -> bool:
        raw = last_seen.get(finding_id)
        if not raw:
            return False
        try:
            return datetime.date.fromisoformat(raw) >= cutoff
        except ValueError:
            return False

    new_ids = [i for i in current_ids if not recently_seen(i)]

    for finding_id in current_ids:
        last_seen[finding_id] = today.isoformat()

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "last_seen": last_seen,
            "written_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
    except OSError:
        log.warning("Could not remember today's findings; tomorrow everything "
                    "will look new.")

    return new_ids


# ---------------------------------------------------------------------------
# Sending it
# ---------------------------------------------------------------------------

def send_digest(cfg: Config | None = None, language: str | None = None,
                for_date: datetime.date | None = None) -> list:
    """
    Build the digest and deliver it every way that is switched on.

    Returns one result per channel. A channel that fails does not stop the
    others, and nothing here raises.
    """
    from .channels import build_channels

    cfg = cfg or get_config()
    lang = normalise(language or cfg.digest_language())

    digest = build_digest(cfg, for_date=for_date)

    results = []
    for channel in build_channels(cfg):
        if not channel.enabled:
            log.debug("Digest channel %r is switched off.", channel.name)
            continue
        result = channel.deliver(digest, lang)
        results.append(result)
        log.info("Digest %s", result)

    return results


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Produce and send the daily digest.")
    parser.add_argument("--lang", choices=["en", "fr", "ar"], default=None)
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print it instead of sending it.")
    args = parser.parse_args()

    from .config import setup_logging
    cfg = get_config()
    setup_logging(cfg)

    target = None
    if args.date:
        target = datetime.datetime.strptime(args.date, "%Y-%m-%d").date()

    lang = normalise(args.lang or cfg.digest_language())

    if args.dry_run:
        digest = build_digest(cfg, for_date=target)
        print(digest.text(lang))
        return 0

    results = send_digest(cfg, language=args.lang, for_date=target)
    print()
    for r in results:
        print(f"  {r}")
    print()
    return 0 if any(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
