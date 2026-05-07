"""
amazon_download.py - Amazon Business order document downloader + folder renamer.

Prerequisites:
    pip install playwright pymupdf
    playwright install chromium

Usage:
    python amazon_download.py
        Opens a GUI. Destination folder is remembered between runs.

    python amazon_download.py --dest "D:/My Invoices" --period previous-month
        CLI mode: starts immediately. --dest is required in CLI mode.

    python amazon_download.py --dest "D:/My Invoices" --from 2026-04-01 --to 2026-04-30
        CLI mode with custom date range.

    python amazon_download.py --dest "D:/My Invoices" --rename-only --period previous-month
        Skips download and renames existing PDFs for the selected period.

    python amazon_download.py --headed
        Opens a visible browser window (useful for debugging).

Run amazon_auth.py whenever Amazon logs you out. This script also launches
amazon_auth.py automatically when it detects an expired Amazon session.
"""


import argparse
import asyncio
import calendar
import json
import queue
import re
import shutil
import sys
import tempfile
import threading
import tkinter as tk
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, scrolledtext
from urllib.parse import urlencode

try:
    import fitz  # pymupdf
    HAS_FITZ = True
except ImportError:
    HAS_FITZ = False

from playwright.async_api import async_playwright


# Paths
# BASE_DIR is always the folder containing this script, regardless of how
# OneDrive is mapped (drive letter, UNC path, or junction) on any machine.
BASE_DIR     = Path(__file__).parent
SESSION_FILE = BASE_DIR / "amazon_session.json"
DOWNLOAD_DIR = BASE_DIR / "_downloads"

# Per-user config — remembers the last chosen destination folder
CONFIG_FILE  = Path.home() / ".amazon_invoice_downloader.json"

DATE_FMT     = "%Y-%m-%d"
GUI_DATE_FMT = "%d/%m/%Y"

MONTH_FOLDER = {
    4: "01. April",  5: "02. May",    6: "03. Jun",    7: "04. Jul",
    8: "05. Aug",    9: "06. Sep",   10: "07. Oct",   11: "08. Nov",
   12: "09. Dec",    1: "10. Jan",    2: "11. Feb",    3: "12. March",
}

ORDER_ID_RE = re.compile(r"[A-Z0-9]{3}-\d{7}-\d{7}", re.IGNORECASE)

SELLER_PATTERNS = [
    # Specific Amazon Business invoice wording seen in PDFs:
    # "Sold by and invoiced on behalf of: Cocoblu Retail"
    # This must be checked before the broader "Sold by" pattern.
    r"Sold by\s+and\s+invoiced\s+on\s+behalf\s+of\s*[:\-]?\s*([^\n\r]+)",

    # Other common invoice labels.
    r"Dispatched and sold by\s*[:\-]?\s*([^\n\r]+)",
    r"Sold by\s*[:\-]?\s*([^\n\r]+)",
    r"Sold By\s*[:\-]?\s*([^\n\r]+)",
    r"Seller\s*[:\-]?\s*([^\n\r]+)",
    r"Supplier\s*[:\-]?\s*([^\n\r]+)",
]

# ── Config (persists destination folder across runs) ───────────────────────

def load_config() -> dict:
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config(data: dict):
    try:
        CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def default_dest() -> Path:
    """Return the last-used destination, or ~/Documents on first run."""
    cfg = load_config()
    saved = cfg.get("dest_folder", "")
    if saved:
        p = Path(saved)
        if p.exists():
            return p
    return Path.home() / "Documents"


# Global log callback, replaced by GUI when running in GUI mode.
_log_cb = print


class SessionExpiredError(Exception):
    pass


@dataclass(frozen=True)
class DateRange:
    """A date period selected by the user or by a predefined option.

    Attributes:
        start:
            First date of the period.

        end:
            Last date of the period.

        source:
            A short internal name such as "last-month", "current-fy", or
            "cli". It helps the GUI and logs display user-friendly labels.

    Non-technical note:
        All predefined periods are converted into exact start/end dates.
        The script then fills those dates in Amazon's Custom Range filter.
    """
    start: date
    end: date
    source: str = "custom"

    @property
    def period_name(self) -> str:
        """Return the display name for the selected period."""
        return PERIOD_LABELS.get(self.source, "Custom Range")

    @property
    def label(self) -> str:
        """Return a readable label for GUI and logs."""
        if self.source in ("current-fy", "last-fy"):
            start_year = self.start.year if self.start.month >= 4 else self.start.year - 1
            return f"{self.period_name} - FY {start_year}-{str(start_year + 1)[-2:]}"
        if is_full_calendar_month(self.start, self.end):
            return f"{self.period_name} - {MONTH_FOLDER[self.start.month]} {self.start.year}"
        return f"{self.period_name} - {format_gui_date(self.start)} to {format_gui_date(self.end)}"

    @property
    def folder_name(self) -> str:
        """Return the subfolder name created under the FY folder."""
        if self.source == "last-fy":
            return "Full FY"
        if self.source == "current-fy":
            return "FY to Date"
        if is_full_calendar_month(self.start, self.end):
            return f"{MONTH_FOLDER[self.start.month]} {self.start.year}"
        return f"Custom {self.start.isoformat()} to {self.end.isoformat()}"

def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.") from exc


def parse_gui_date(value: str) -> date:
    try:
        return datetime.strptime(value, GUI_DATE_FMT).date()
    except ValueError as exc:
        raise ValueError(f"Invalid date '{value}'. Use DD/MM/YYYY.") from exc


def format_gui_date(value: date) -> str:
    return value.strftime(GUI_DATE_FMT)


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year  = value.year + month_index // 12
    month = month_index % 12 + 1
    day   = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def validate_date_range(start: date, end: date):
    if start > end:
        raise ValueError("From date cannot be after To date.")
    if end > date.today():
        raise ValueError("To date cannot be after today; Amazon disables future dates.")


def current_month_range(today: date | None = None) -> DateRange:
    """Return the current month from the 1st day up to today."""
    today = today or date.today()
    return DateRange(start=today.replace(day=1), end=today, source="current-month")


def previous_month_range(today: date | None = None) -> DateRange:
    """Backward-compatible alias for last_month_range()."""
    return last_month_range(today)


def last_month_range(today: date | None = None) -> DateRange:
    """Return the immediately preceding full calendar month."""
    today = today or date.today()
    first_day_this_month = today.replace(day=1)
    end   = first_day_this_month - timedelta(days=1)
    start = end.replace(day=1)
    return DateRange(start=start, end=end, source="last-month")


def month_to_date_range(today: date | None = None) -> DateRange:
    """Backward-compatible alias for current_month_range()."""
    return current_month_range(today)


def current_quarter_range(today: date | None = None) -> DateRange:
    """Return the current calendar quarter up to today.

    Calendar quarters:
        Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec.
    """
    today = today or date.today()
    quarter_start_month = ((today.month - 1) // 3) * 3 + 1
    return DateRange(
        start=date(today.year, quarter_start_month, 1),
        end=today,
        source="current-quarter",
    )


def current_fy_range(today: date | None = None) -> DateRange:
    """Return the current Indian financial year up to today."""
    today = today or date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return DateRange(
        start=date(start_year, 4, 1),
        end=today,
        source="current-fy",
    )


def last_fy_range(today: date | None = None) -> DateRange:
    """Return the immediately preceding full Indian financial year."""
    today = today or date.today()
    current_start_year = today.year if today.month >= 4 else today.year - 1
    return DateRange(
        start=date(current_start_year - 1, 4, 1),
        end=date(current_start_year, 3, 31),
        source="last-fy",
    )


def last_12_months_range(today: date | None = None) -> DateRange:
    """Return a rolling 12-month period ending today."""
    today = today or date.today()
    start = _add_months(today, -12) + timedelta(days=1)
    return DateRange(start=start, end=today, source="last-12-months")


PERIOD_FACTORIES = {
    "current-month": current_month_range,
    "last-month": last_month_range,
    "previous-month": last_month_range,
    "month-to-date": current_month_range,
    "current-quarter": current_quarter_range,
    "current-fy": current_fy_range,
    "last-fy": last_fy_range,
    "last-12-months": last_12_months_range,
}


PERIOD_LABELS = {
    "current-month": "Current Month",
    "last-month": "Last Month",
    "previous-month": "Last Month",
    "month-to-date": "Current Month",
    "current-quarter": "Current Quarter",
    "current-fy": "Current FY",
    "last-fy": "Last FY",
    "last-12-months": "Last 12 months",
    "gui": "Custom Range",
    "cli": "Custom Range",
    "custom": "Custom Range",
}


def is_full_calendar_month(start: date, end: date) -> bool:
    last_day = calendar.monthrange(start.year, start.month)[1]
    return start.day == 1 and end == start.replace(day=last_day)


def get_fy_dir(anchor: date | None = None, dest_root: Path | None = None) -> Path:
    """Return the Indian Financial Year folder under dest_root (or BASE_DIR)."""
    anchor = anchor or date.today()
    start  = anchor.year if anchor.month >= 4 else anchor.year - 1
    root   = dest_root or BASE_DIR
    return root / f"FY {start}-{str(start + 1)[-2:]}"


def get_period_dir(period: DateRange, dest_root: Path | None = None) -> Path:
    return get_fy_dir(period.start, dest_root) / period.folder_name


def get_last_month_folder() -> str:
    return previous_month_range().folder_name


def clean_seller(name: str) -> str:
    name = re.sub(r"\(seller profile\)", "", name, flags=re.I)
    name = re.sub(r"[®™©]", "", name)
    name = name.replace(".", "")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    return name.strip(" -_")


def extract_seller(pdf_path: Path) -> str | None:
    """Try to extract seller name from a PDF invoice.

    Main seller source:
        The Amazon report table has a reliable "Seller Name" column. The
        script uses that table first.

    PDF fallback:
        This function is used only when the table does not provide seller
        name for a particular order.

    Important Amazon wording handled here:
        "Sold by and invoiced on behalf of: Cocoblu Retail"
    """
    if not HAS_FITZ:
        return None
    try:
        doc  = fitz.open(str(pdf_path))
        text = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as exc:
        _log_cb(f"  [PDF error] {pdf_path.name}: {exc}")
        return None
    if not text.strip():
        _log_cb(f"  [image PDF] {pdf_path.name}: no extractable text")
        return None
    for pat in SELLER_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if not m:
            continue
        raw = re.split(
            r"\n|,|GST|PAN|CIN|GSTIN|\|",
            m.group(1).strip(),
        )[0].strip()
        seller = clean_seller(raw)
        if seller and seller.lower() not in {"and", "seller profile"}:
            return seller
    return None

def rename_order_folder(order_dir: Path, seller: str) -> bool:
    name = order_dir.name
    base = name.split(" - ")[0]
    if not ORDER_ID_RE.fullmatch(base):
        _log_cb(f"  SKIP (unexpected name): {name}")
        return False
    new_name = (
        f"{name} - {seller}"
        if " - " not in name
        else name.rsplit(" - ", 1)[0] + f" - {seller}"
    )
    new_path = order_dir.parent / new_name
    if new_path == order_dir:
        _log_cb(f"  SKIP (already named): {name}")
        return False
    if new_path.exists():
        _log_cb(f"  SKIP (target exists): {new_name}")
        return False
    order_dir.rename(new_path)
    _log_cb(f"  OK {name} -> {new_name}")
    return True


_JS_SCRAPE = """
async () => {
    const el = document.querySelector('.report-table') || 
               document.querySelector('.report-table-container');
    if (!el) return [];
    
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const fullData = {}; // rowIdx -> { colTitle -> text }
    let allHeaders = new Set();

    const scan = () => {
        const cols = Array.from(document.querySelectorAll('.report-table .column'));
        const titles = cols.map(c => {
            const h = c.querySelector('.cell-header, .header-cell, [role="columnheader"], .column-header');
            if (!h) return '';
            const clone = h.cloneNode(true);
            clone.querySelectorAll('.in-context-help, .help, svg, .a-popover-trigger').forEach(e => e.remove());
            return (clone.innerText || clone.textContent || '').trim();
        });
        
        cols.forEach((col, colIdx) => {
            const title = titles[colIdx];
            if (!title) return;
            allHeaders.add(title);
            
            const cells = col.querySelectorAll('.cell:not(.cell-header)');
            cells.forEach((cell, rowIdx) => {
                let text = (cell.textContent || '').trim();
                if (title.toLowerCase().includes('order id')) {
                    const m = text.match(/[A-Z0-9]{3}-\\d{7}-\\d{7}/i);
                    if (m) text = m[0];
                }
                if (text) {
                    if (!fullData[rowIdx]) fullData[rowIdx] = {};
                    fullData[rowIdx][title] = text;
                }
            });
        });
    };

    // 4-Step horizontal sweep
    const maxScroll = el.scrollWidth - el.clientWidth;
    for (let i = 0; i <= 4; i++) {
        el.scrollLeft = (maxScroll / 4) * i;
        await sleep(400); // Wait for lazy load
        scan();
    }
    
    // Return to start
    el.scrollLeft = 0;

    const result = [{ _headers: Array.from(allHeaders) }];
    const rowIndexes = Object.keys(fullData).sort((a,b) => a-b);
    
    for (const rIdx of rowIndexes) {
        const row = fullData[rIdx];
        let hasCancel = false;
        for (const val of Object.values(row)) {
            if (val.toLowerCase().includes('cancelled') || val.toLowerCase().includes('canceled')) {
                hasCancel = true; break;
            }
        }
        if (hasCancel) {
            const sKey = Object.keys(row).find(k => k.toLowerCase().includes('status'));
            if (sKey && !row[sKey]) row[sKey] = 'Cancelled';
            else if (!sKey) row['_internal_status'] = 'Cancelled';
        }
        result.push(row);
    }
    return result;
}
"""


_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_LOWER = "abcdefghijklmnopqrstuvwxyz"


async def _click_first(candidates, timeout: int = 2500) -> bool:
    for locator in candidates:
        try:
            await locator.wait_for(state="visible", timeout=timeout)
            await locator.click()
            return True
        except Exception:
            continue
    return False


async def _fill_first_date_input(candidates, target: date, timeout: int = 1500) -> bool:
    for locator in candidates:
        try:
            await locator.wait_for(state="visible", timeout=timeout)
            value = await _date_value_for_input(locator, target)
            await locator.click()
            await locator.fill(value)
            await locator.evaluate(
                """
                (el, value) => {
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(el, value);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.blur();
                }
                """,
                value,
            )
            return True
        except Exception:
            continue
    return False


async def _date_value_for_input(locator, target: date) -> str:
    attrs      = []
    input_type = ""
    for attr in ("type", "placeholder", "aria-label", "name", "id"):
        try:
            value = await locator.get_attribute(attr) or ""
            attrs.append(value)
            if attr == "type":
                input_type = value.lower()
        except Exception:
            pass
    meta = " ".join(attrs).lower()
    dd   = meta.find("dd")
    mm   = meta.find("mm")
    yyyy = meta.find("yyyy")
    if input_type == "date":
        return target.isoformat()
    if yyyy >= 0 and mm >= 0 and dd >= 0 and yyyy < mm < dd:
        return target.isoformat()
    if dd >= 0 and mm >= 0 and dd < mm:
        return target.strftime("%d/%m/%Y")
    if mm >= 0 and dd >= 0 and mm < dd:
        return target.strftime("%m/%d/%Y")
    return target.strftime("%d/%m/%Y")


def _attr_contains(page, attr: str, token: str):
    return page.locator(
        "xpath=//input[contains("
        f"translate(@{attr}, '{_UPPER}', '{_LOWER}'), "
        f"'{token.lower()}')]"
    ).first


async def _fill_date_kind(page, kind: str, target: date) -> bool:
    label  = "Start date" if kind == "start" else "End date"
    tokens = {"start": ("start", "from", "begin"), "end": ("end", "to")}[kind]

    candidates = [
        page.locator(f"xpath=//*[normalize-space()='{label}']/following::input[1]").first,
        page.locator(f"xpath=//p[normalize-space()='{label}']/following-sibling::*//input[1]").first,
    ]
    for token in tokens:
        candidates.extend([
            page.get_by_label(re.compile(token, re.IGNORECASE)).first,
            page.get_by_placeholder(re.compile(token, re.IGNORECASE)).first,
        ])
        for attr in ("name", "id", "aria-label", "placeholder"):
            candidates.append(_attr_contains(page, attr, token))

    if await _fill_first_date_input(candidates, target):
        return True

    try:
        inputs = page.locator(".b-date-picker input:visible, .react-datepicker-wrapper input:visible")
        index  = 0 if kind == "start" else 1
        if await inputs.count() > index:
            return await _fill_first_date_input([inputs.nth(index)], target)
    except Exception:
        pass
    return False


async def _looks_like_session_expired(page) -> bool:
    url = page.url.lower()
    if "signin" in url or "/ap/signin" in url:
        return True
    for selector in ("input#ap_email", "input#ap_password", "form[name='signIn']"):
        try:
            if await page.locator(selector).count():
                return True
        except Exception:
            pass
    for text in ("Session expired", "Your session has expired"):
        try:
            await page.get_by_text(re.compile(text, re.IGNORECASE)).first.wait_for(
                state="visible", timeout=750
            )
            return True
        except Exception:
            pass
    return False


async def _raise_if_session_expired(page):
    if await _looks_like_session_expired(page):
        raise SessionExpiredError("Session expired - refreshing with amazon_auth.py.")


def build_report_url(period: DateRange) -> str:
    params = {
        "reportType": "items_report_1",
        "dateSpanSelection": "MONTH_TO_DATE",
        "ref": "hpr_redirect_report",
    }
    return "https://www.amazon.in/b2b/aba/reports?" + urlencode(params)


async def _open_order_date_dropdown(page) -> bool:
    candidates = [
        page.locator("#date_range_selector__range").first,
        page.locator("button#date_range_selector__range").first,
        page.locator("button.b-dropdown-toggle[id*='date_range']").first,
        page.locator("xpath=//*[normalize-space()='Order Date']/following::button[contains(@class,'b-dropdown-toggle')][1]").first,
        page.locator("xpath=//*[normalize-space()='Time period']/following::button[contains(@class,'b-dropdown-toggle')][1]").first,
    ]
    return await _click_first(candidates, timeout=3500)


async def _select_custom_range_option(page) -> bool:
    try:
        if await page.locator(
            "xpath=//*[normalize-space()='Start date']/following::input[1]"
        ).first.count():
            return True
    except Exception:
        pass
    try:
        current = (
            await page.locator(
                "xpath=//*[normalize-space()='Time period']/following::button[1]"
            ).first.inner_text(timeout=1000)
        ).strip()
        if "Custom Range" in current:
            return True
    except Exception:
        pass
    candidates = [
        page.get_by_text("Custom Range", exact=True).last,
        page.locator("text=Custom Range").last,
    ]
    return await _click_first(candidates, timeout=3500)


async def _click_apply_date_range(page) -> bool:
    candidates = [
        page.get_by_role("button", name=re.compile(r"^(Apply|Update|Submit|Done)$", re.IGNORECASE)).first,
        page.locator("button:has-text('Apply')").first,
        page.locator("button:has-text('Update')").first,
        page.locator("input[type='submit']").first,
    ]
    return await _click_first(candidates, timeout=2500)


async def _set_date_filter(page, period: DateRange, screenshot_path: Path) -> bool:
    """
    Set Amazon report date range through Custom Range.

    Non-technical explanation:
        The user may choose Last Month, Current FY, Last 12 months, etc.
        The script first converts that choice into exact From/To dates and
        then fills those dates in Amazon's Custom Range box.

    Why:
        Amazon's own predefined options were not reliable in automation.
    """
    await _raise_if_session_expired(page)

    if not await _open_order_date_dropdown(page):
        await page.screenshot(path=str(screenshot_path), full_page=True)
        _log_cb("ERROR: Could not open Time period dropdown.")
        _log_cb(f"       Screenshot: {screenshot_path.name}")
        return False

    await page.wait_for_timeout(500)
    _log_cb(f"Selecting custom range: {period.start.isoformat()} to {period.end.isoformat()}")

    if not await _select_custom_range_option(page):
        await page.screenshot(path=str(screenshot_path), full_page=True)
        _log_cb("ERROR: Could not select Custom Range in dropdown.")
        _log_cb(f"       Screenshot: {screenshot_path.name}")
        return False

    await page.wait_for_timeout(800)

    start_ok = await _fill_date_kind(page, "start", period.start)
    end_ok   = await _fill_date_kind(page, "end",   period.end)
    if not start_ok or not end_ok:
        await page.screenshot(path=str(screenshot_path), full_page=True)
        _log_cb("ERROR: Could not fill Custom Range dates.")
        _log_cb(f"       Screenshot: {screenshot_path.name}")
        return False

    if not await _click_apply_date_range(page):
        await page.screenshot(path=str(screenshot_path), full_page=True)
        _log_cb("ERROR: Could not apply Custom Range dates.")
        _log_cb(f"       Screenshot: {screenshot_path.name}")
        return False

    try:
        await page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        pass
    await page.wait_for_timeout(2_500)
    await _raise_if_session_expired(page)
    return True




async def download_order_docs(
    session_file: Path,
    dl_dir: Path,
    period_dir: Path,
    period: DateRange,
    headed: bool = False,
) -> dict:
    """
    Downloads order documents page-by-page and returns a master seller map.

    Workflow:
      1. Navigate to items report.
      2. Set date filter.
      3. For each page:
         a. Scrape order -> seller names.
         b. Select all rows on current page.
         c. Trigger 'Download selected' and capture ZIP.
         d. Extract ZIP into period_dir.
         e. Click 'Next'.
    """
    dl_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = BASE_DIR / "_debug_screenshot.png"
    url = build_report_url(period)
    master_map: dict = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=not headed,
            slow_mo=200 if headed else 80,
            args=["--window-size=1440,900"] + (["--start-maximized"] if headed else []),
        )
        try:
            ctx = await browser.new_context(
                storage_state=str(session_file),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                accept_downloads=True,
                viewport={"width": 1440, "height": 900},
            )
            page = await ctx.new_page()

            _log_cb("Connecting to Amazon ...")
            await page.goto(url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(3000)
            await _raise_if_session_expired(page)

            _log_cb("Page loaded.")

            if not await _set_date_filter(page, period, screenshot_path):
                return {}

            page_num   = 1
            master_map = {}
            all_data   = [] # For CSV
            
            while True:
                _log_cb(f"\n[Page {page_num}] Processing ...")
                await page.wait_for_timeout(3500)
                
                # Scrolling to load virtual rows (Vertical + Horizontal)
                await page.evaluate("""() => {
                    const el = document.querySelector('.report-table') || 
                               document.querySelector('.report-table-container');
                    if (el) {
                        // Vertical
                        el.scrollTop = el.scrollHeight;
                        // Horizontal to the end and back
                        el.scrollLeft = el.scrollWidth;
                        setTimeout(() => { el.scrollLeft = 0; }, 500);
                    }
                }""")
                await page.wait_for_timeout(2000)

                try:
                    await page.locator(".report-table").first.wait_for(
                        state="visible", timeout=20_000
                    )
                except Exception:
                    break

                # 1. Scrape
                page_items = await page.evaluate(_JS_SCRAPE)
                if page_items:
                    # Check for debug headers
                    if "_headers" in page_items[0]:
                        hdrs = page_items.pop(0)["_headers"]
                        _log_cb(f"  Columns: {', '.join([h for h in hdrs if h])}")

                    # Filter out ghost rows immediately
                    real_page_items = []
                    for item in page_items:
                        oid_val = ""
                        for k, v in item.items():
                            if "order id" in k.lower():
                                oid_val = v; break
                        if oid_val:
                            real_page_items.append(item)
                    
                    page_items = real_page_items
                    valid_on_page = 0
                    for item in page_items:
                        all_data.append(item)
                        
                        # Find Order ID and Status keys
                        oid = ""
                        sts = ""
                        sel = ""
                        for k, v in item.items():
                            kl = k.lower()
                            if "order id" in kl: oid = v
                            if "status" in kl or "_internal" in kl: sts = v
                            if "seller" in kl:   sel = v
                        
                        # Filtering
                        _log_cb(f"  Order {oid} status: '{sts}'")
                        
                        if oid.startswith('D'):
                            _log_cb(f"  Skipping {oid} (D-prefix)")
                            continue
                        if "cancel" in sts.lower():
                            _log_cb(f"  Skipping {oid} (Cancelled)")
                            continue
                        
                        if oid:
                            master_map[oid] = sel
                            valid_on_page += 1
                    
                    _log_cb(f"  {valid_on_page} valid order(s) detected (out of {len(page_items)})")

                # 2. Select All then Deselect (Faster approach)
                try:
                    header_cb = page.locator(".report-table .cell-header .b-checkbox").first
                    if await header_cb.is_visible():
                        await header_cb.click()
                        await page.wait_for_timeout(1000)
                        
                        # Deselect items we don't want
                        checkbox_col = page.locator(".report-table .column").first
                        checkboxes   = checkbox_col.locator(".cell:not(.cell-header)")
                        
                        for i, item in enumerate(page_items):
                            oid = ""
                            sts = ""
                            for k, v in item.items():
                                kl = k.lower()
                                if "order id" in kl: oid = v
                                if "status"   in kl: sts = v
                                
                            if oid.startswith('D') or "cancel" in sts.lower():
                                if i < await checkboxes.count():
                                    cb = checkboxes.nth(i)
                                    await cb.click()
                                    await page.wait_for_timeout(300)
                        
                        dl_btn = page.locator("#download-order-documents-dropdown").first
                        await dl_btn.click()
                        await page.wait_for_timeout(1000)
                        
                        dl_sel = page.locator("[data-testid='download-selected-order-documents']").first
                        if await dl_sel.get_attribute("disabled") is None:
                            async with page.expect_download(timeout=120_000) as dl_info:
                                await dl_sel.click()
                            download = await dl_info.value
                            zip_name = f"page_{page_num}_{datetime.now().strftime('%H%M%S')}.zip"
                            zip_path = dl_dir / zip_name
                            await download.save_as(str(zip_path))
                            process_zip(zip_path, period_dir, rename=False)
                except Exception as exc:
                    _log_cb(f"  Selection/Download issue: {exc}")

                # 3. Next
                next_btn = page.locator("[data-testid='next-button']:not([status='disabled'])").first
                if await next_btn.count() == 0 or await next_btn.get_attribute("status") == "disabled":
                    break
                await next_btn.click()
                page_num += 1

            # Save Excel Summary
            if all_data:
                excel_path = period_dir / "report_summary.xlsx"
                try:
                    import pandas as pd
                    import numpy as np
                    df = pd.DataFrame(all_data)
                    
                    # Drop specific columns requested by user
                    forbidden = {
                        "order shipping & handling", "account user", "account user email",
                        "payment identifier", "product condition", "received date"
                    }
                    to_drop = [c for c in df.columns if c.strip().lower() in forbidden]
                    df = df.drop(columns=to_drop, errors='ignore')
                    
                    # Drop columns that are entirely empty or just whitespace
                    df = df.replace(r'^\s*$', np.nan, regex=True)
                    df = df.dropna(axis=1, how='all')
                    
                    # Fill remaining NaNs with empty string
                    df = df.fillna('')
                    
                    df.to_excel(excel_path, index=False)
                    _log_cb(f"\nOK Full Excel summary saved to: {excel_path.name}")
                except Exception as exc:
                    _log_cb(f"\nWarning: Could not save Excel - {exc}")
                    # Fallback to CSV if pandas is not available
                    csv_path = period_dir / "report_summary.csv"
                    try:
                        import csv
                        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                            if all_data:
                                # Simple filter for CSV as well
                                forbidden_csv = {
                                    "order shipping & handling", "account user", "account user email",
                                    "payment identifier", "product condition", "received date"
                                }
                                keys = [
                                    k for k in all_data[0].keys() 
                                    if k.strip().lower() not in forbidden_csv and any(row.get(k) for row in all_data)
                                ]
                                writer = csv.DictWriter(f, fieldnames=keys, extrasaction='ignore')
                                writer.writeheader()
                                writer.writerows(all_data)
                        _log_cb(f"OK Fallback CSV saved to: {csv_path.name}")
                    except Exception:
                        pass

            return master_map

        finally:
            await browser.close()


def process_zip(zip_path: Path, period_dir: Path, seller_map: dict | None = None, rename: bool = True):
    tmp = Path(tempfile.mkdtemp(prefix="amazon_extract_"))

    _log_cb("\nExtracting archive ...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp)

    entries       = list(tmp.iterdir())
    order_folders = [e for e in entries if e.is_dir()  and ORDER_ID_RE.search(e.name)]
    loose_pdfs    = [e for e in entries if e.is_file() and e.suffix.lower() == ".pdf"]
    _log_cb(f"  {len(order_folders)} order folder(s) + {len(loose_pdfs)} loose PDF(s)")

    period_dir.mkdir(parents=True, exist_ok=True)

    for src_dir in order_folders:
        m        = ORDER_ID_RE.search(src_dir.name)
        order_id = m.group(0) if m else src_dir.name
        dest_dir = period_dir / order_id
        if dest_dir.exists():
            for f in src_dir.iterdir():
                if f.is_file():
                    shutil.copy2(str(f), str(dest_dir / f.name))
        else:
            shutil.copytree(str(src_dir), str(dest_dir))
        
        if rename:
            seller = clean_seller((seller_map or {}).get(order_id, "")) or None
            if not seller:
                pdfs   = list(dest_dir.glob("*.pdf"))
                seller = extract_seller(pdfs[0]) if pdfs else None
            if seller:
                rename_order_folder(dest_dir, seller)

    for pdf in loose_pdfs:
        m        = ORDER_ID_RE.search(pdf.stem)
        order_id = m.group(0) if m else pdf.stem
        dest_dir = period_dir / order_id
        dest_dir.mkdir(exist_ok=True)
        dest_file = dest_dir / pdf.name
        shutil.copy2(str(pdf), str(dest_file))
        
        if rename:
            seller = clean_seller((seller_map or {}).get(order_id, "")) or None
            if not seller:
                seller = extract_seller(dest_file)
            if seller:
                rename_order_folder(dest_dir, seller)

    shutil.rmtree(tmp, ignore_errors=True)
    zip_path.unlink(missing_ok=True)


def rename_existing(period_dir: Path, seller_map: dict | None = None):
    if not period_dir.exists():
        _log_cb(f"Folder not found: {period_dir}")
        return
    order_dirs = [d for d in period_dir.iterdir() if d.is_dir()]
    _log_cb(f"Renaming {len(order_dirs)} order folder(s) in {period_dir.name} ...")
    for order_dir in sorted(order_dirs):
        m        = ORDER_ID_RE.search(order_dir.name)
        order_id = m.group(0) if m else order_dir.name
        
        seller = clean_seller((seller_map or {}).get(order_id, "")) or None
        if not seller:
            pdfs = list(order_dir.glob("*.pdf"))
            if pdfs:
                seller = extract_seller(pdfs[0])
        
        if seller:
            rename_order_folder(order_dir, seller)
        else:
            _log_cb(f"  -- {order_dir.name}: seller not found")


# ── GUI ────────────────────────────────────────────────────────────────────

APP_TITLE = "Amazon Business Invoice Downloader by CA. Deepak Bhholusaria (c) 2026"


class DownloadWindow:
    BG      = "#f0f2f5"
    CARD    = "#ffffff"
    ACCENT  = "#e47911"
    TEXT    = "#1a1a2e"
    SUBTEXT = "#666666"
    SUCCESS = "#2e7d32"
    ERROR   = "#c62828"
    DIM     = "#aaaaaa"
    LOG_BG  = "#f8f9fa"
    LOG_FG  = "#333333"
    W, H    = 850, 780

    STEPS = [
        "Connecting to Amazon",
        "Selecting date range",
        "Downloading documents",
        "Extracting archive",
        "Renaming folders",
    ]

    def __init__(
        self,
        rename_only: bool = False,
        date_range: DateRange | None = None,
        auto_start: bool = False,
        headed: bool = False,
        dest_root: Path | None = None,
    ):
        global _log_cb
        self._rename_only = rename_only
        self._date_range  = date_range or last_month_range()
        self._auto_start  = auto_start
        self._headed      = headed
        self._dest_root   = dest_root or default_dest()
        self._msg_q       = queue.Queue()
        self._done        = False
        self._success     = False
        self._worker_started    = False
        self._control_widgets: list[tk.Widget] = []
        self._calendar_popup: tk.Toplevel | None = None
        self._calendar_month = date.today().replace(day=1)

        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.configure(bg=self.BG)
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(700, 600)
        self._centre()

        self._build()
        _log_cb = self._enqueue

        self.root.after(100, self._drain_queue)
        if self._auto_start:
            self.root.after(250, self._start_worker)

        self.root.mainloop()

    def _centre(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{self.W}x{self.H}+{(sw-self.W)//2}+{(sh-self.H)//2}")

    def _build(self):
        tk.Frame(self.root, bg=self.ACCENT, height=6).pack(fill="x")

        tk.Label(
            self.root, text="Amazon Business Invoice Downloader",
            bg=self.BG, fg=self.ACCENT, font=("Segoe UI", 16, "bold"),
        ).pack(pady=(12, 0))

        tk.Label(
            self.root, text="by CA. Deepak Bhholusaria  •  © 2026",
            bg=self.BG, fg=self.SUBTEXT, font=("Segoe UI", 9),
        ).pack(pady=(0, 4))

        self._processing_var = tk.StringVar(value=f"Processing: {self._date_range.label}")
        tk.Label(
            self.root, textvariable=self._processing_var,
            bg=self.BG, fg=self.TEXT, font=("Segoe UI", 10),
        ).pack(pady=(0, 6))

        if not self._auto_start:
            self._build_period_controls()

        step_frame = tk.Frame(self.root, bg=self.CARD, padx=20, pady=12)
        step_frame.pack(fill="x", padx=24, pady=(0, 10))

        self._step_var = tk.StringVar(value="Ready to start")
        tk.Label(step_frame, textvariable=self._step_var, bg=self.CARD, fg=self.TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")

        self._progress = ttk.Progressbar(step_frame, orient="horizontal", mode="determinate")
        self._progress.pack(fill="x", pady=(8, 0))

        tk.Label(self.root, text="Log", bg=self.BG, fg=self.SUBTEXT,
                 font=("Segoe UI", 10)).pack(anchor="w", padx=26, pady=(2, 2))

        self._log = scrolledtext.ScrolledText(
            self.root, bg=self.LOG_BG, fg=self.LOG_FG,
            font=("Consolas", 10), relief="solid", borderwidth=1,
            state="disabled", wrap="word", height=35,
        )
        self._log.pack(fill="both", expand=True, padx=24, pady=(0, 6))
        self._log.tag_config("ok",  foreground=self.SUCCESS)
        self._log.tag_config("err", foreground=self.ERROR)
        self._log.tag_config("dim", foreground=self.SUBTEXT)

        status_text = "Starting ..." if self._auto_start else "Select period and click Start."
        self._status_var = tk.StringVar(value=status_text)
        self._status_lbl = tk.Label(
            self.root, textvariable=self._status_var,
            bg=self.BG, fg=self.SUBTEXT, font=("Segoe UI", 10),
        )
        self._status_lbl.pack(pady=(0, 4))

        self._close_btn = tk.Button(
            self.root, text="Close",
            bg=self.DIM, fg=self.TEXT, font=("Segoe UI", 10),
            relief="flat", padx=20, pady=6, state="disabled",
            command=self.root.destroy,
        )
        self._close_btn.pack(pady=(0, 14))

    def _build_period_controls(self):
        """Build the top GUI controls.

        Controls shown to the user:
            1. Destination folder
            2. Reporting period dropdown
            3. From/To dates
            4. Browser mode checkbox

        Non-technical note:
            For predefined periods, the From/To boxes are filled
            automatically and locked. For Custom Range, they become editable.
        """
        panel = tk.Frame(self.root, bg=self.CARD, padx=16, pady=14)
        panel.pack(fill="x", padx=24, pady=(0, 10))
        self._control_widgets.append(panel)

        # Destination folder
        tk.Label(panel, text="Save invoices to", bg=self.CARD, fg=self.SUBTEXT,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", pady=(0, 4))

        self._dest_var = tk.StringVar(value=str(self._dest_root))
        dest_entry = tk.Entry(
            panel, textvariable=self._dest_var, width=50,
            font=("Segoe UI", 10), bg="#f8f9fa", fg=self.TEXT,
            insertbackground=self.TEXT, relief="solid", borderwidth=1,
        )
        dest_entry.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))

        self._browse_btn = tk.Button(
            panel, text="Browse ...",
            bg=self.ACCENT, fg="white", font=("Segoe UI", 10),
            relief="flat", padx=12, pady=4, command=self._browse_dest,
        )
        self._browse_btn.grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(0, 10))

        # Middle: Consolidated Controls
        tk.Label(panel, text="Period", bg=self.CARD, fg=self.SUBTEXT,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w")
        tk.Label(panel, text="From", bg=self.CARD, fg=self.SUBTEXT,
                 font=("Segoe UI", 9)).grid(row=2, column=1, sticky="w", padx=(10, 0))
        tk.Label(panel, text="To", bg=self.CARD, fg=self.SUBTEXT,
                 font=("Segoe UI", 9)).grid(row=2, column=2, sticky="w", padx=(10, 0))

        self._period_options = [
            ("Current Month", "current-month"),
            ("Last Month", "last-month"),
            ("Current Quarter", "current-quarter"),
            ("Current FY", "current-fy"),
            ("Last FY", "last-fy"),
            ("Last 12 months", "last-12-months"),
            ("Custom Range", "custom"),
        ]
        self._period_display_to_key = dict(self._period_options)
        self._period_var = tk.StringVar(value="Last Month")
        self._period_combo = ttk.Combobox(
            panel, textvariable=self._period_var,
            values=[l for l, _ in self._period_options],
            state="readonly", width=18
        )
        self._period_combo.grid(row=3, column=0, sticky="w", pady=(2, 0))
        self._period_combo.bind("<<ComboboxSelected>>", lambda _e: self._sync_period_mode())

        # Dates
        last_month = last_month_range()
        self._from_var = tk.StringVar(value=format_gui_date(last_month.start))
        self._to_var   = tk.StringVar(value=format_gui_date(last_month.end))

        f_box = tk.Frame(panel, bg=self.CARD)
        t_box = tk.Frame(panel, bg=self.CARD)
        f_box.grid(row=3, column=1, sticky="w", padx=(10, 0), pady=(2, 0))
        t_box.grid(row=3, column=2, sticky="w", padx=(10, 0), pady=(2, 0))

        self._from_entry = tk.Entry(f_box, textvariable=self._from_var, width=11, font=("Consolas", 10))
        self._to_entry   = tk.Entry(t_box, textvariable=self._to_var,   width=11, font=("Consolas", 10))
        self._from_picker_btn = tk.Button(f_box, text="v", width=2, command=lambda: self._open_date_picker(self._from_var, self._from_picker_btn))
        self._to_picker_btn   = tk.Button(t_box, text="v", width=2, command=lambda: self._open_date_picker(self._to_var, self._to_picker_btn))

        self._from_entry.pack(side="left")
        self._from_picker_btn.pack(side="left", padx=(2, 0))
        self._to_entry.pack(side="left")
        self._to_picker_btn.pack(side="left", padx=(2, 0))
        
        self._start_btn = tk.Button(
            panel, text="Start", bg=self.ACCENT, fg="white",
            font=("Segoe UI", 11, "bold"), relief="flat", padx=30, pady=6,
            command=self._start_worker
        )
        self._start_btn.grid(row=3, column=3, sticky="e", padx=(20, 0), pady=(2, 0))
        panel.grid_columnconfigure(2, weight=1)

        # Checkbox line
        self._headed_var = tk.BooleanVar(value=self._headed)
        tk.Checkbutton(
            panel, text="Show browser window (debug/headed mode)",
            variable=self._headed_var, bg=self.CARD, fg=self.TEXT,
            activebackground=self.CARD, font=("Segoe UI", 9)
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

        for widget in (
            dest_entry, self._period_combo, self._from_entry, self._to_entry,
            self._from_picker_btn, self._to_picker_btn, self._start_btn, self._browse_btn,
        ):
            self._control_widgets.append(widget)

        self._sync_period_mode()

    def _browse_dest(self):
        if self._worker_started:
            return
        folder = filedialog.askdirectory(
            title="Select destination folder for invoices",
            initialdir=self._dest_var.get(),
            parent=self.root,
        )
        if folder:
            self._dest_var.set(folder)

    def _sync_period_mode(self):
        """Update date boxes and Processing label when user changes period."""
        selected_label = self._period_var.get()
        selected_key = self._period_display_to_key.get(selected_label, "last-month")

        if selected_key == "custom":
            state = "normal"
            selected = self._collect_date_range_from_gui(show_error=False)
            if selected:
                self._date_range = selected
        else:
            selected = PERIOD_FACTORIES[selected_key]()
            self._date_range = selected
            self._from_var.set(format_gui_date(selected.start))
            self._to_var.set(format_gui_date(selected.end))
            state = "disabled"

        self._from_entry.config(state=state)
        self._to_entry.config(state=state)
        self._from_picker_btn.config(state=state)
        self._to_picker_btn.config(state=state)
        self._processing_var.set(f"Processing: {self._date_range.label}")

    def _collect_date_range_from_gui(self, show_error: bool = True) -> DateRange | None:
        """Read the selected period/date range from the GUI.

        Returns None only when the user entered an invalid custom date range.
        """
        try:
            selected_label = self._period_var.get()
            selected_key = self._period_display_to_key.get(selected_label, "last-month")

            if selected_key != "custom":
                return PERIOD_FACTORIES[selected_key]()

            start = parse_gui_date(self._from_var.get().strip())
            end   = parse_gui_date(self._to_var.get().strip())
            validate_date_range(start, end)
            return DateRange(start=start, end=end, source="gui")

        except ValueError as exc:
            if show_error:
                messagebox.showerror("Invalid date range", str(exc), parent=self.root)
            return None

    def _open_date_picker(self, target_var: tk.StringVar, anchor: tk.Widget):
        if self._worker_started:
            return
        if self._calendar_popup and self._calendar_popup.winfo_exists():
            self._calendar_popup.destroy()
        try:
            selected = parse_gui_date(target_var.get().strip())
        except ValueError:
            selected = date.today()
        self._calendar_month = selected.replace(day=1)
        popup = tk.Toplevel(self.root)
        popup.title("Select date")
        popup.configure(bg=self.CARD)
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.attributes("-topmost", True)
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + 2
        popup.geometry(f"+{x}+{y}")
        popup.bind("<Escape>", lambda _e: popup.destroy())
        self._calendar_popup = popup
        self._draw_calendar(target_var)

    def _draw_calendar(self, target_var: tk.StringVar):
        popup = self._calendar_popup
        if not popup or not popup.winfo_exists():
            return
        for child in popup.winfo_children():
            child.destroy()

        header = tk.Frame(popup, bg=self.CARD, padx=8, pady=6)
        header.pack(fill="x")
        tk.Button(header, text="<", bg=self.DIM, fg=self.TEXT, relief="flat", width=3,
                  command=lambda: self._shift_calendar_month(target_var, -1)).pack(side="left")
        tk.Label(header, text=self._calendar_month.strftime("%B %Y"),
                 bg=self.CARD, fg=self.TEXT, font=("Segoe UI", 9, "bold"),
                 width=18).pack(side="left", padx=8)
        next_month  = _add_months(self._calendar_month, 1)
        next_state  = "disabled" if next_month > date.today().replace(day=1) else "normal"
        tk.Button(header, text=">", bg=self.DIM, fg=self.TEXT, relief="flat", width=3,
                  state=next_state,
                  command=lambda: self._shift_calendar_month(target_var, 1)).pack(side="left")

        grid = tk.Frame(popup, bg=self.CARD, padx=8, pady=8)
        grid.pack()
        for col, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            tk.Label(grid, text=name, bg=self.CARD, fg=self.SUBTEXT,
                     font=("Segoe UI", 8), width=4).grid(row=0, column=col, pady=(0, 3))

        cal = calendar.Calendar(firstweekday=0)
        for row, week in enumerate(
            cal.monthdatescalendar(self._calendar_month.year, self._calendar_month.month),
            start=1,
        ):
            for col, day in enumerate(week):
                if day.month != self._calendar_month.month:
                    tk.Label(grid, text=str(day.day), bg=self.CARD, fg="#bbbbbb",
                             font=("Segoe UI", 8), width=4).grid(row=row, column=col, padx=1, pady=1)
                    continue
                state = "disabled" if day > date.today() else "normal"
                bg    = self.ACCENT if day == date.today() else "#f0f2f5"
                fg    = "white"     if day == date.today() else self.TEXT
                tk.Button(
                    grid, text=str(day.day), bg=bg, fg=fg,
                    disabledforeground="#bbbbbb", relief="flat", width=4, state=state,
                    command=lambda picked=day: self._select_calendar_date(target_var, picked),
                ).grid(row=row, column=col, padx=1, pady=1)

    def _shift_calendar_month(self, target_var: tk.StringVar, delta: int):
        self._calendar_month = _add_months(self._calendar_month, delta)
        self._draw_calendar(target_var)

    def _select_calendar_date(self, target_var: tk.StringVar, selected: date):
        target_var.set(format_gui_date(selected))
        if self._calendar_popup and self._calendar_popup.winfo_exists():
            self._calendar_popup.destroy()
        selected_range = self._collect_date_range_from_gui(show_error=False)
        if selected_range:
            self._date_range = selected_range
            self._processing_var.set(f"Processing: {self._date_range.label}")

    def _start_worker(self):
        """Validate user inputs and start the actual download in a worker thread.

        If the destination path is unavailable, the GUI shows a clear popup and
        lets the user choose a different folder instead of failing silently.
        """
        if self._worker_started:
            return

        if not self._auto_start:
            selected = self._collect_date_range_from_gui()
            if selected is None:
                return
            self._date_range = selected

            dest_str = self._dest_var.get().strip()
            if not dest_str:
                messagebox.showerror("No destination", "Please select a destination folder.", parent=self.root)
                return

            dest_path = Path(dest_str)

            while True:
                try:
                    # Create/check the root destination folder and also the FY/period
                    # folder now. This catches OneDrive or invalid path problems before
                    # the download begins.
                    dest_path.mkdir(parents=True, exist_ok=True)
                    test_period_dir = get_period_dir(self._date_range, dest_path)
                    test_period_dir.mkdir(parents=True, exist_ok=True)
                    break
                except Exception as exc:
                    messagebox.showerror(
                        "Destination path error",
                        "The selected destination could not be created or accessed.\n\n"
                        f"Selected path:\n{dest_path}\n\n"
                        f"Error:\n{exc}\n\n"
                        "Please select a valid destination folder.",
                        parent=self.root,
                    )
                    folder = filedialog.askdirectory(
                        title="Select a valid destination folder",
                        initialdir=str(Path.home()),
                        parent=self.root,
                    )
                    if not folder:
                        return
                    dest_path = Path(folder)
                    self._dest_var.set(str(dest_path))

            self._dest_root = dest_path
            self._headed = bool(self._headed_var.get())
            save_config({"dest_folder": str(dest_path)})

        self._worker_started = True
        self._done           = False
        self._success        = False
        self._progress["value"] = 0
        self._step_var.set("Starting ...")
        self._processing_var.set(f"Processing: {self._date_range.label}")
        self._status_var.set("Working ...")
        self._status_lbl.config(fg=self.SUBTEXT)

        for widget in self._control_widgets:
            try:
                widget.config(state="disabled")
            except Exception:
                pass

        threading.Thread(target=self._run_logic, daemon=True).start()

    def _enqueue(self, msg: str):
        self._msg_q.put(str(msg))

    def _drain_queue(self):
        try:
            while True:
                msg = self._msg_q.get_nowait()
                self._append_log(msg)
                self._update_steps(msg)
        except queue.Empty:
            pass
        if self._done:
            self._on_done()
        else:
            self.root.after(100, self._drain_queue)

    def _append_log(self, msg: str):
        self._log.config(state="normal")
        stripped = msg.strip()
        tag = (
            "ok"  if stripped.startswith("OK") or "All done" in stripped else
            "err" if "ERROR" in stripped.upper() else
            "dim"
        )
        self._log.insert("end", msg.rstrip() + "\n", tag)
        self._log.see("end")
        self._log.config(state="disabled")

    def _update_steps(self, msg: str):
        for i, step_name in enumerate(self.STEPS):
            if step_name.lower() in msg.lower():
                self._update_step(i)
                
    def _update_step(self, step_idx: int):
        """Update the progress bar and status text."""
        if 0 <= step_idx < len(self.STEPS):
            lbl = self.STEPS[step_idx]
            self._step_var.set(f"Step {step_idx+1}/{len(self.STEPS)}: {lbl}")
            self._progress["value"] = ((step_idx + 1) / len(self.STEPS)) * 100
            self.root.update_idletasks()

    def _on_done(self):
        if self._success:
            self._progress["value"] = 100
            self._status_var.set("All done.")
            self._status_lbl.config(fg=self.SUCCESS)
        else:
            self._status_var.set("Finished with errors - check log above.")
            self._status_lbl.config(fg=self.ERROR)
            
        self._close_btn.config(state="normal", bg=self.ACCENT, fg="white", cursor="hand2")
        
        # RE-ENABLE all controls for the next run
        for widget in self._control_widgets:
            try:
                widget.config(state="normal")
            except Exception:
                pass
        self._worker_started = False
        self._start_btn.config(state="normal")

    def _run_logic(self):
        try:
            asyncio.run(self._async_logic())
            self._success = True
        except SystemExit as exc:
            self._enqueue(f"ERROR: {exc}")
        except Exception as exc:
            self._enqueue(f"ERROR: {exc}")
        finally:
            self._done = True

    async def _async_logic(self):
        period     = self._date_range
        period_dir = get_period_dir(period, self._dest_root)
        self._enqueue(f"Target folder: {period_dir}")
        self._enqueue(f"Date range: {period.start.isoformat()} to {period.end.isoformat()}\n")

        if self._rename_only:
            self._enqueue("Mode: rename-only (skipping download)\n")
            rename_existing(period_dir)
        else:
            if not HAS_FITZ:
                self._enqueue(
                    "WARNING: pymupdf not installed - seller names will not be extracted.\n"
                    "         Run: pip install pymupdf\n"
                )
            seller_map = await self._download_with_auth_retry(period, period_dir)
            if not seller_map:
                return  # already logged or empty
            
            self._enqueue("\nRenaming all folders ...")
            rename_existing(period_dir, seller_map)

        self._enqueue("\nOK All done.")

    async def _download_with_auth_retry(self, period: DateRange, period_dir: Path) -> dict:
        for attempt in range(2):
            try:
                if not SESSION_FILE.exists() and attempt == 0:
                    raise SessionExpiredError("No saved session file.")
                return await download_order_docs(
                    SESSION_FILE, DOWNLOAD_DIR, period_dir, period, headed=self._headed
                )
            except SessionExpiredError as exc:
                if attempt == 1:
                    raise SystemExit("Session still expired after re-auth. Aborting.") from exc
                self._enqueue("\nSession expired - launching amazon_auth.py ...")
                self._enqueue("Log in to Amazon and click 'Save Session' in the popup window.\n")
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, str(BASE_DIR / "amazon_auth.py")
                )
                await proc.wait()
                if not SESSION_FILE.exists():
                    raise SystemExit("amazon_auth.py closed without saving a session.")
                self._enqueue("Session saved - retrying download ...\n")
        return None, {}


# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="amazon_download.py",
        description=(
            "Amazon Business Invoice Downloader\n"
            "Downloads order documents from Amazon Business Analytics and\n"
            "organises them into FY / month folders, named by seller.\n\n"
            "GUI mode  : run without --period / --from / --to\n"
            "CLI mode  : pass --dest (required) plus a period flag"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dest", metavar="FOLDER",
        help=(
            "Destination mother folder where FY sub-folders are created. "
            "Required in CLI mode. Example: --dest \"D:/My Invoices\""
        ),
    )
    parser.add_argument(
        "--rename-only", action="store_true",
        help="Skip download; rename existing PDF folders by seller name only.",
    )
    parser.add_argument(
        "--headed", action="store_true",
        help="Open a visible browser window (useful for debugging).",
    )
    parser.add_argument(
        "--no-gui", dest="no_gui", action="store_true",
        help=(
            "Run in pure CLI mode — no window, all output to stdout. "
            "Ideal for scheduled tasks / batch files."
        ),
    )
    parser.add_argument(
        "--period", choices=tuple(sorted(PERIOD_FACTORIES.keys())),
        help="Predefined period. Triggers CLI (non-interactive) mode.",
    )
    parser.add_argument(
        "--from", dest="from_date", metavar="YYYY-MM-DD",
        help="Custom range start date. Use with --to. Triggers CLI mode.",
    )
    parser.add_argument(
        "--to", dest="to_date", metavar="YYYY-MM-DD",
        help="Custom range end date. Use with --from. Triggers CLI mode.",
    )
    return parser.parse_args(argv)


def period_from_args(args) -> DateRange | None:
    """Convert command-line period inputs into exact From/To dates.

    This function is deliberately simple:
        - If --from and --to are supplied, use those exact dates.
        - If --period is supplied, calculate exact dates for that period.
        - If neither is supplied, GUI will default to Last Month.
    """
    if args.from_date or args.to_date:
        if not args.from_date or not args.to_date:
            raise ValueError("Use both --from YYYY-MM-DD and --to YYYY-MM-DD.")
        start = parse_date(args.from_date)
        end   = parse_date(args.to_date)
        validate_date_range(start, end)
        return DateRange(start=start, end=end, source="cli")

    if args.period:
        factory = PERIOD_FACTORIES.get(args.period)
        if not factory:
            raise ValueError(f"Unsupported period: {args.period}")
        return factory()

    return None

def show_startup_error(title: str, body: str):
    root = tk.Tk()
    root.title(title)
    root.configure(bg="#f0f2f5")
    root.geometry("560x280")
    tk.Label(root, text=title, bg="#f0f2f5", fg="#c62828",
             font=("Segoe UI", 12, "bold")).pack(pady=(20, 4))
    txt = tk.Text(root, bg="#ffffff", fg="#1a1a2e", font=("Consolas", 10),
                  wrap="word", relief="solid", borderwidth=1, padx=8, pady=8)
    txt.insert("1.0", body)
    txt.config(state="disabled")
    txt.pack(fill="both", expand=True, padx=20, pady=(0, 16))
    root.mainloop()


_NOTIFY_APP_ID = "AmazonInvoiceDownloader"
_NOTIFY_SCHEME  = "amazon-inv"   # custom URL scheme used by action buttons


def _windows_notify(
    title: str,
    message: str,
    icon: str = "Info",
    action_label: str | None = None,
    action_script: Path | None = None,
):
    """
    Show a persistent Windows 10/11 toast notification.

    - Registers the app ID in HKCU so WinRT accepts it.
    - If action_label + action_script are given, registers a custom URL scheme
      (amazon-inv://) and adds a clickable button to the toast that launches
      the script directly from the notification.
    - Falls back to a legacy balloon tip if the WinRT path fails.
    - Fire-and-forget — never raises, never delays the main process.
    """
    if sys.platform != "win32":
        return

    import subprocess
    import tempfile

    def xml_esc(s: str) -> str:
        """Escape special chars for embedding in XML attribute / text."""
        return (s.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))

    def ps1_esc(s: str) -> str:
        """Escape a value for embedding inside a PowerShell single-quoted string."""
        return s.replace("'", "''")

    t_xml = xml_esc(title)
    m_xml = xml_esc(message)

    # ── Optional action button ────────────────────────────────────────────
    actions_xml  = ""
    scheme_block = ""

    if action_label and action_script:
        exe     = ps1_esc(str(sys.executable))   # e.g. C:\Python311\python.exe
        scr     = ps1_esc(str(action_script))     # e.g. D:\Codex\...\amazon_auth.py
        cmd_val = ps1_esc(f'{sys.executable} "{action_script}" "%1"')
        al_xml  = xml_esc(action_label)
        scheme  = _NOTIFY_SCHEME

        scheme_block = f"""
    # Register custom URL scheme so the button can launch the script
    $s = 'HKCU:\\SOFTWARE\\Classes\\{scheme}'
    New-Item -Path $s -Force | Out-Null
    New-ItemProperty -Path $s -Name '(Default)'    -Value 'URL:Amazon Invoice Downloader' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $s -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
    $c = 'HKCU:\\SOFTWARE\\Classes\\{scheme}\\shell\\open\\command'
    New-Item -Path $c -Force | Out-Null
    New-ItemProperty -Path $c -Name '(Default)' -Value '{cmd_val}' -PropertyType String -Force | Out-Null
"""
        actions_xml = (
            f'<actions>'
            f'<action content="{al_xml}" activationType="protocol" arguments="{scheme}://run" />'
            f'</actions>'
        )

    toast_xml = (
        f'<toast>'
        f'<visual><binding template="ToastGeneric">'
        f'<text>{t_xml}</text><text>{m_xml}</text>'
        f'</binding></visual>'
        f'{actions_xml}'
        f'</toast>'
    )

    t_ps = ps1_esc(title)
    m_ps = ps1_esc(message)
    txml_ps = ps1_esc(toast_xml)

    ps = f"""
$ErrorActionPreference = 'Stop'
try {{
    # Register app ID so Windows accepts the notifier
    $reg = 'HKCU:\\SOFTWARE\\Classes\\AppUserModelId\\{_NOTIFY_APP_ID}'
    if (-not (Test-Path $reg)) {{
        New-Item -Path $reg -Force | Out-Null
        New-ItemProperty -Path $reg -Name 'DisplayName' -Value 'Amazon Invoice Downloader' -PropertyType String -Force | Out-Null
    }}
    {scheme_block}
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml('{txml_ps}')
    $toast    = [Windows.UI.Notifications.ToastNotification]::new($xml)
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{_NOTIFY_APP_ID}')
    $notifier.Show($toast)
    Start-Sleep -Seconds 10
}} catch {{
    # Fallback: legacy balloon tip (5 s, no action button)
    Add-Type -AssemblyName System.Windows.Forms
    $n = New-Object System.Windows.Forms.NotifyIcon
    $n.Icon    = [System.Drawing.SystemIcons]::Information
    $n.Visible = $true
    $n.ShowBalloonTip(5000, '{t_ps}', '{m_ps}', 'Info')
    Start-Sleep -Seconds 5
    $n.Dispose()
}}
"""
    try:
        tmp = Path(tempfile.gettempdir()) / "amazon_inv_notify.ps1"
        tmp.write_text(ps, encoding="utf-8")
        subprocess.Popen(
            [
                "powershell", "-NoProfile", "-WindowStyle", "Hidden",
                "-ExecutionPolicy", "Bypass", "-File", str(tmp),
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass  # Notification is best-effort; never crash the main flow


async def cli_run(period: DateRange, dest_root: Path, rename_only: bool, headed: bool):
    """Pure-CLI execution path — no GUI, all output to stdout with timestamps."""
    global _log_cb

    def ts_print(msg: str):
        ts = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
        for line in str(msg).splitlines():
            print(f"[{ts}] {line}", flush=True)

    _log_cb = ts_print
    period_dir = get_period_dir(period, dest_root)

    ts_print(f"Period      : {period.label}")
    ts_print(f"Date range  : {period.start.isoformat()} to {period.end.isoformat()}")
    ts_print(f"Destination : {period_dir}")
    ts_print(f"Headed      : {headed}")
    ts_print("")

    try:
        period_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        msg = (
            "Destination path is invalid or inaccessible. "
            f"Please verify --dest. Failed folder: {period_dir}. Error: {exc}"
        )
        ts_print(f"ERROR: {msg}")
        _windows_notify(
            "Amazon Invoices - Destination Path Error",
            msg,
            icon="Warning",
        )
        sys.exit(1)

    if rename_only:
        ts_print("Mode: rename-only (skipping download)")
        rename_existing(period_dir)
        ts_print("Done.")
        return

    if not HAS_FITZ:
        ts_print("WARNING: pymupdf not installed — seller names from PDFs won't be extracted.")
        ts_print("         Run: pip install pymupdf")

    seller_map: dict = {}

    auth_script = BASE_DIR / "amazon_auth.py"

    if not SESSION_FILE.exists():
        msg = "No saved session. Click 'Refresh Session' or run amazon_auth.py manually."
        ts_print(f"ERROR: No saved session found. Expected: {SESSION_FILE}")
        ts_print(f"       {msg}")
        _windows_notify(
            "Amazon Invoices — Action Required", msg, icon="Warning",
            action_label="Refresh Session", action_script=auth_script,
        )
        sys.exit(1)

    try:
        seller_map = await download_order_docs(
            SESSION_FILE, DOWNLOAD_DIR, period_dir, period, headed=headed
        )
        if not seller_map:
            print("No invoices were downloaded.")
            return

        print("\nRenaming all folders ...")
        rename_existing(period_dir, seller_map)
    except SessionExpiredError as exc:
        msg = "Amazon session expired. Click 'Refresh Session' or run Amazon Session Refresher manually."
        ts_print(f"ERROR: Session expired ({exc})")
        ts_print(f"       {msg}")
        _windows_notify(
            "Amazon Invoices-Action Required", msg, icon="Warning",
            action_label="Refresh Session", action_script=auth_script,
        )
        sys.exit(1)

    ts_print("")
    ts_print("All done.")
    _windows_notify(
        "Amazon Invoices — Done",
        f"{period.label}: {len(seller_map)} order(s) downloaded and organised.",
        icon="Info",
    )


def main(argv: list[str] | None = None):
    args            = parse_args(argv)
    selected_period = period_from_args(args)
    auto_start      = bool(selected_period or args.rename_only)

    dest_root: Path | None = None
    if args.dest:
        dest_root = Path(args.dest)
        if not dest_root.exists():
            try:
                dest_root.mkdir(parents=True)
            except Exception as exc:
                msg = f"Cannot create destination folder: {dest_root}. Error: {exc}"
                print(f"ERROR: {msg}", file=sys.stderr)
                _windows_notify(
                    "Amazon Invoices - Destination Path Error",
                    msg,
                    icon="Warning",
                )
                sys.exit(1)
    elif auto_start:
        print(
            "ERROR: --dest is required in CLI mode.\n"
            "Example: python amazon_download.py "
            "--dest \"D:/My Invoices\" --period previous-month",
            file=sys.stderr,
        )
        sys.exit(1)

    if selected_period is None:
        selected_period = last_month_range()

    if args.no_gui:
        if not dest_root:
            print("ERROR: --dest is required with --no-gui.", file=sys.stderr)
            sys.exit(1)
        asyncio.run(cli_run(selected_period, dest_root, args.rename_only, args.headed))
    else:
        DownloadWindow(
            rename_only=args.rename_only,
            date_range=selected_period,
            auto_start=auto_start,
            headed=args.headed,
            dest_root=dest_root,
        )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        show_startup_error("Amazon Invoice Downloader - Error", traceback.format_exc())
