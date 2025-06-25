import json, os, sys, subprocess, requests
from packaging.version import parse as parse_version

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

# --- Configuration (now only GitHub repo details) ---
GITHUB_REPO_OWNER = "izaz4141"
GITHUB_REPO_NAME = "Nadeko-don"

def get_platform_app_name() -> str:
    """
    Determines the expected application name based on the operating system.
    """
    if sys.platform == 'win32':
        return 'nadeko-don.exe'
    elif sys.platform == 'linux':
        return 'nadeko-don'
    # Add more platforms if needed, or default to a common name
    else:
        # Fallback for other operating systems, or raise an error if not supported
        return 'unknown' # Example for a generic name

def fetch_release_info(release_data: dict) -> dict | None:
    """
    Extracts tag_name and the appropriate download_url from a single release's data.
    """
    latest_tag = release_data.get("tag_name")
    download_url = None
    app_name = get_platform_app_name()

    for asset in release_data.get("assets", []):
        if asset.get("name") == app_name:
            download_url = asset.get("browser_download_url")
            break

    if latest_tag and download_url:
        return {"tag_name": latest_tag, "download_url": download_url}
    return None

def get_all_github_releases() -> list[dict]:
    """
    Fetches all releases from a GitHub repository, handling pagination.
    Returns a list of release dictionaries.
    """
    all_releases = []
    page = 1
    headers = {"Accept": "application/vnd.github.v3+json"}

    while True:
        api_url = (
            f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases"
            f"?per_page=100&page={page}" # GitHub API default per_page is 30, max is 100
        )
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        releases_page = response.json()

        if not releases_page:
            # No more releases on this page, stop fetching
            break

        all_releases.extend(releases_page)
        page += 1

    return all_releases

def get_github_release_info() -> dict | None:
    """
    Fetches release information from a GitHub repository.
    First tries to get the latest release. If a suitable asset is not found,
    it then iterates through all releases from newest to oldest.

    Returns a dictionary containing 'tag_name' (version) and 'download_url' for the asset,
    or None if an error occurs or no suitable asset is found after checking all releases.
    """
    headers = {"Accept": "application/vnd.github.v3+json"}
    app_name = get_platform_app_name() # Get the app name once
    if app_name == 'unknown':
        return None

    print(f"Attempting to find release for app: '{app_name}' on OS: '{sys.platform}'")

    try:
        # 1. Try to fetch the very latest release directly
        latest_release_url = (
            f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}/releases/latest"
        )
        print(f"Checking latest release from: {latest_release_url}")
        response = requests.get(latest_release_url, headers=headers)
        response.raise_for_status()
        latest_release_info = response.json()

        result = fetch_release_info(latest_release_info)
        if result:
            print(f"Found suitable asset in latest release: {result['tag_name']}")
            return result

        print("Suitable asset not found in the very latest release. Checking all releases.")

        # 2. If the latest release doesn't have the asset, fetch all releases
        all_releases = get_all_github_releases()
        if not all_releases:
            print("No releases found for the repository.")
            return None

        # Iterate through all releases from newest to oldest
        # GitHub API usually returns releases in reverse chronological order by default,
        # but explicit iteration ensures this.
        for release in all_releases:
            release_result = fetch_release_info(release)
            if release_result:
                print(f"Found suitable asset in release: {release_result['tag_name']}")
                return release_result

        print(f"No suitable asset '{app_name}' found across all releases for OS '{sys.platform}'.")
        return None

    except requests.exceptions.RequestException as e:
        raise Exception(f"Error fetching GitHub release info: {e}")
        return None
    except json.JSONDecodeError as e:
        raise Exception(f"Error parsing JSON response: {e}")
        return None
    except Exception as e:
        raise Exception(f"An unexpected error occurred: {e}")
        return None

def check_for_updates(current_version: str, logger):
    """
    Checks for updates, downloads if available, and prepares for replacement.
    Takes current_version and app_executable_name as arguments.
    """
    release_info = None
    try:
        release_info = get_github_release_info()
    except Exception as e:
        logger.error(e)

    if not release_info:
        return False, "Failed to retrieve release info.", None

    latest_version_str = release_info["tag_name"]
    download_url = release_info["download_url"]

    try:
        # Use packaging.version.parse for robust version comparison (handles 'v' prefixes, etc.)
        current_version = parse_version(current_version.lstrip('vV'))
        latest_version = parse_version(latest_version_str.lstrip('vV'))

        if latest_version > current_version:
            return True, f"v{current_version} (v{latest_version} is available)", download_url

        else:
            return False, f"v{current_version} (Already on latest)", None
    except Exception as e:
        logger.error(f"v{current_version}: [ERROR] {e}")
        return False, f"v{current_version}: [ERROR] {e}", None

def initiate_self_replacement(new_executable_path: str):
    """
    Conceptual function to handle the self-replacement and relaunch.
    This part is highly platform-dependent and complex for --onefile.
    """

    # Create a small temporary script/batch file that will:
    # 1. Wait for the current app (this PySide6 app) to close.
    # 2. Rename/delete the old executable (current_exe_path).
    # 3. Move the new executable (new_executable_path) into the old executable's place.
    # 4. Relaunch the new executable.

    current_exe_path = sys.executable

    if sys.platform == "win32":
        # Windows: Create a .bat file
        helper_script_content = f"""
        @echo off
        REM Wait for the current app to close (adjust timeout as needed)
        timeout /t 5 /nobreak >nul
        REM Delete old executable
        del "{current_exe_path}"
        REM Move new executable to old path
        move "{new_executable_path}" "{current_exe_path}"
        REM Relaunch the updated app
        start "" "{current_exe_path}"
        REM Clean up the helper script itself (optional, but good practice)
        del "%~f0"
        """
        # Use temp directory for the helper script to avoid permission issues
        temp_dir = os.path.join(os.environ.get("TEMP", os.getcwd()))
        helper_script_path = os.path.join(temp_dir, f"update_helper_{os.getpid()}.bat") # Use PID for uniqueness
        try:
            with open(helper_script_path, "w") as f:
                f.write(helper_script_content)
            subprocess.Popen([helper_script_path], shell=True, creationflags=subprocess.DETACHED_PROCESS)
            print(f"Windows update helper launched: {helper_script_path}")
        except Exception as e:
            raise Exception(f"Error launching Windows update helper: {e}")
    elif sys.platform == 'linux':
        # Linux/macOS: Create a shell script
        helper_script_content = f"""
        #!/bin/bash
        # Wait for the current app to close (adjust sleep as needed)
        sleep 5
        # Delete old executable
        rm "{current_exe_path}"
        # Move new executable to old path
        mv "{new_executable_path}" "{current_exe_path}"
        chmod +x "{current_exe_path}"
        # Relaunch the updated app in background and detach
        {current_exe_path} & disown
        # Clean up the helper script itself
        rm -- "$0"
        """
        temp_dir = "/tmp"
        helper_script_path = os.path.join(temp_dir, f"update_helper_{os.getpid()}.sh")
        try:
            with open(helper_script_path, "w") as f:
                f.write(helper_script_content)
            os.chmod(helper_script_path, 0o755) # Make it executable
            subprocess.Popen(["bash", helper_script_path], close_fds=True, preexec_fn=os.setsid)
            print(f"Linux/macOS update helper launched: {helper_script_path}")
        except Exception as e:
            raise Exception(f"Error launching Linux/macOS update helper: {e}")
    else:
        print("Self-update is not supported on this platform.")
        QMessageBox.critical("Update Error", "Self-update is not supported on your operating system.")


class UpdateWorker(QThread):
    updateDetails = Signal(list)
    def __init__(self, main_window, download_url=False):
        super().__init__()
        self.main_window = main_window
        self.current_ver = QApplication.instance().applicationVersion()
        self.app_name = os.path.basename(sys.executable)
        self.download_url = download_url

    def run(self):
        if not self.download_url:
            updates, message, download_url = check_for_updates(self.current_ver, self.main_window.logger)
            self.updateDetails.emit([updates, message, download_url])
            return
        download_request = {
            'type': 'single',
            'items': [
                {
                    'url': self.download_url,
                    'path': f"{self.main_window.config['save_path']}/{self.app_name}"
                }
            ]
        }
        self.main_window.download_manager.task_finished.connect(self.handle_downloaded)
        self.main_window.download_manager.add_download(download_request)

    def handle_downloaded(self, task):
        try:
            initiate_self_replacement(task.save_path)
        except Exception as e:
            self.main_window.logger.error(e)
        self.main_window.quit_app()
