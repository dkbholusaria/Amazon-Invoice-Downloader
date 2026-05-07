# Amazon Business Invoice Downloader

![Version](https://img.shields.io/badge/version-1.2.0-orange)
![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-green)

A powerful, automated tool designed for CA professionals and business owners to scrape, download, and organize Amazon Business invoices with 100% reliability.

---

## 📸 Preview

| Graphical Interface (GUI) | Command Line Interface (CLI) |
| :---: | :---: |
| ![GUI Screenshot](screenshots/gui.png) | ![CLI Screenshot](screenshots/cli.png) |

---

## 🌟 Key Features

- **🚀 Automated Batch Downloading**: Scrapes multiple pages of Amazon Business Analytics reports and downloads all selected invoices automatically.
- **📁 Smart Folder Organization**: Automatically renames folders from generic IDs to clean, identifiable names: `[Order ID] - [Seller Name]`.
- **📊 Detailed Excel Reporting**: Generates a comprehensive `.xlsx` report of all transactions, including cleaned Order IDs, Sellers, and Statuses.
- **🔍 Advanced PDF Extraction**: If a seller name is missing from the table, the tool "looks inside" the PDF invoice to extract the correct seller name using OCR/Text extraction.
- **🛡️ Intelligent Filtering**: Automatically skips "Cancelled" orders and digital/system orders (D-prefix) to keep your records clean.
- **📟 Modern GUI & CLI**: Includes a user-friendly graphical interface with a progress bar and a detailed log window, as well as a robust Command Line Interface for power users.
- **🎯 Virtualization Support**: Implements "Step-Scan" technology to handle extremely wide Amazon tables (50+ columns) by forcing horizontal lazy-loading.

---

## 🛠️ Installation

### 🚀 The Fast Way (Standalone EXE)
1.  Download **`AmazonInvoiceDownloader.exe`** from the [Releases](https://github.com/dkbholusaria/Amazon-Invoice-Downloader/releases) page.
2.  Run the EXE. It will automatically set up its own browser environment on the first run.

### 🛠️ The Developer Way (Python)
1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/dkbholusaria/Amazon-Invoice-Downloader.git
    cd Amazon-Invoice-Downloader
    ```
2.  **Install Dependencies**: `pip install -r requirements.txt`
3.  **Setup Playwright**: `playwright install chromium`

---

## 🔐 Authentication Setup

Amazon Business requires a secure session. This tool uses a persistent session file to avoid repeated logins.

1. Run the authentication script:
   ```bash
   python amazon_auth.py
   ```
2. A browser window will open. Log in to your Amazon Business account and navigate to the **Business Analytics** page.
3. Once the page is loaded, the script will save your session securely to your user profile folder (`~/amazon_invoice_downloader/amazon_session.json`) and close.

> [!TIP]
> **CLI / Scheduled Tasks**: If your session expires during an automated run, the tool will send a native **Windows Notification**. You can click the **"Refresh Session"** button directly in the notification to log in without needing to run any manual commands.

---

## 🚀 How to Use

### Graphical Interface (GUI)
Run the EXE or the main script:
```bash
# Using EXE
./AmazonInvoiceDownloader.exe

# Using Python
python amazon_download_complete_documented.py
```
- Select your **Destination Folder**.
- Choose a **Reporting Period**.
- Click **Start**.

### Command Line Interface (CLI)
Ideal for automated workflows and batch files:
```bash
# Using EXE
./AmazonInvoiceDownloader.exe --dest "C:/Invoices" --period last-month

# Using Python
python amazon_download_complete_documented.py --dest "C:/Invoices" --period last-month
```
**Options:**
- `--dest`: Destination folder (Required)
- `--period`: `current-month`, `last-month`, `current-fy`, `last-fy`, etc.
- `--from` / `--to`: Custom date range (YYYY-MM-DD)
- `--headed`: Show the browser window.
- `--no-gui`: Force CLI mode (default if period is provided).

---

## 📁 Project Structure

- `AmazonInvoiceDownloader.exe`: Standalone portable application.
- `amazon_download_complete_documented.py`: Core application logic (Python).
- `amazon_auth.py`: Session management helper.
- `build.py`: Script to rebuild the EXE.
- `screenshots/`: App previews.
- `.gitignore`: Prevents private data leaks.
- **Private Data (User Profile)**:
  - `~/amazon_invoice_downloader/`: All private user data is consolidated here.
    - `amazon_session.json`: Your secure login session.
    - `config.json`: Remembers your last used settings/folders.
    - `run.log`: Detailed run logs.
    - `temp_downloads/`: Temporary extraction space.

---

## ⚖️ License

Distributed under the MIT License. See `LICENSE` for more information.

---

## 👨‍💻 Developed By

**Deepak Bholusaria (CA)**
[![GitHub](https://img.shields.io/badge/GitHub-dkbholusaria-black?style=flat&logo=github)](https://github.com/dkbholusaria)

© 2026

*Disclaimer: This tool is for personal and professional use. Always ensure compliance with Amazon's Terms of Service when using automation tools.*
