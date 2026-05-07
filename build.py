import sys
import subprocess
from pathlib import Path
from PIL import Image

def convert_icon(png_path, ico_path):
    print(f"Converting {png_path} to {ico_path}...")
    img = Image.open(png_path)
    img.save(ico_path, format='ICO', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])

def build_exe():
    base_dir = Path(__file__).parent
    png_icon = Path(r"C:\Users\deepa\.gemini\antigravity\brain\963b54ce-9863-488e-b716-dcf90b972b5a\app_icon_1778187726606.png")
    ico_icon = base_dir / "app_icon.ico"
    
    if png_icon.exists():
        convert_icon(png_icon, ico_icon)
    
    main_script = base_dir / "amazon_download_complete_documented.py"
    
    cmd = [
        "pyinstaller",
        "--onefile",
        "--noconsole",
        f"--name=AmazonInvoiceDownloader",
        f"--icon={ico_icon}" if ico_icon.exists() else "",
        "--add-data=amazon_auth.py;.", # Include auth script as source
        "--hidden-import=amazon_auth",
        "--hidden-import=playwright.async_api",
        "--collect-all=playwright",
        str(main_script)
    ]
    
    # Remove empty strings from cmd
    cmd = [c for c in cmd if c]
    
    print("Running PyInstaller...")
    subprocess.run(cmd, check=True)
    print("\nBuild completed successfully!")
    print(f"Your EXE is in the 'dist' folder.")

if __name__ == "__main__":
    build_exe()
