import sys
import subprocess
from pathlib import Path

def build_exe():
    # Automatically resolve the project root (one level up from scripts/)
    base_dir = Path(__file__).parent.parent
    
    ico_icon = base_dir / "app_icon.ico"
    main_script = base_dir / "amazon_download_complete_documented.py"
    
    # Handle platform-specific PyInstaller path separator (; on Windows, : on Linux)
    separator = ";" if sys.platform.startswith("win") else ":"
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        f"--name=AmazonInvoiceDownloader",
        f"--icon={ico_icon}" if ico_icon.exists() else "",
        f"--add-data=amazon_auth.py{separator}.", # Include auth script as source
        "--hidden-import=amazon_auth",
        "--hidden-import=playwright.async_api",
        "--collect-all=playwright",
        str(main_script)
    ]
    
    # Remove empty strings from cmd
    cmd = [c for c in cmd if c]
    
    print(f"Running PyInstaller in project root: {base_dir}...")
    subprocess.run(cmd, cwd=base_dir, check=True)
    print("\nBuild completed successfully!")
    print(f"Your executable is in the 'dist' folder.")

if __name__ == "__main__":
    build_exe()
