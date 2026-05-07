# Command Line Interface (CLI) Guide

This guide provides detailed instructions for using the Amazon Business Invoice Downloader via the command line. This is ideal for power users, developers, or anyone looking to automate their monthly financial workflows.

---

## 🛠️ Basic Syntax

To use the CLI, run the main script with the desired arguments:

```bash
python amazon_download_complete_documented.py [arguments]
```

---

## 📝 Available Arguments

| Argument | Description | Required | Example |
| :--- | :--- | :--- | :--- |
| `--dest` | The root folder where invoices will be saved. | **Yes** | `--dest "D:/MyInvoices"` |
| `--period` | Predefined date range to download. | No | `--period last-month` |
| `--from` | Start date (DD/MM/YYYY) for custom range. | No* | `--from 01/01/2026` |
| `--to` | End date (DD/MM/YYYY) for custom range. | No* | `--to 31/01/2026` |
| `--rename-only` | Skips downloading and only renames existing folders. | No | `--rename-only` |
| `--headed` | Shows the browser window during processing (Debug mode). | No | `--headed` |

*\*Required if using a custom date range.*

---

## 📅 Supported Periods

When using the `--period` argument, you can use any of the following keys:

- `current-month`: From the 1st of the current month to today.
- `last-month`: The entire previous calendar month.
- `current-quarter`: The current financial quarter.
- `current-fy`: The current Financial Year (starts April 1st).
- `last-fy`: The previous Financial Year.
- `last-12-months`: The last 365 days.

---

## 💡 Practical Examples

### 1. Download Last Month's Invoices (The "Monthly Run")
This is the most common use case for monthly bookkeeping.
```bash
python amazon_download_complete_documented.py --dest "D:/Accounts/Invoices" --period last-month
```

### 2. Download a Specific Custom Range
Ideal for tax audits or specific project reviews.
```bash
python amazon_download_complete_documented.py --dest "D:/Audit" --from 01/01/2026 --to 15/01/2026
```

### 3. Rename Folders Only
If you already have folders with Order IDs and just want to add the Seller Names without re-downloading everything.
```bash
python amazon_download_complete_documented.py --dest "D:/MyInvoices" --period last-fy --rename-only
```

---

## 🤖 Automation: Windows Task Scheduler

You can automate the tool to run every month on the 5th (once Amazon has finalized all invoices).

1. Create a `.bat` file (e.g., `auto_download.bat`):
   ```batch
   @echo off
   cd /d "D:\Codex\Amazon-Invoice-Downloader"
   python amazon_download_complete_documented.py --dest "D:\Invoices" --period last-month
   pause
   ```
2. Open **Windows Task Scheduler**.
3. Create a **New Basic Task** and set it to run monthly.
4. Point the action to your `.bat` file.

---

## ⚠️ Important Notes

- **Session Required**: The CLI mode requires a valid session. If your session expires, run `python amazon_auth.py` once to log in manually.
- **Headless by Default**: In CLI mode, the browser runs in the background (headless) to be as fast as possible. Use `--headed` if you want to see the browser in action.
- **Error Logs**: If a CLI run fails, check the console output or the `report_summary.xlsx` generated in the target folder for transaction details.
