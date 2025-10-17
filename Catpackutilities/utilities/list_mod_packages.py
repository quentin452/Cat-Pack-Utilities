import os
import zipfile
import logging
from datetime import datetime

# --- Configuration ---
LOG_FOLDER = 'packagelogs'
CONFIG_FILE_NAME = 'list_packages_config.txt'

# --- Setup Logging ---
os.makedirs(LOG_FOLDER, exist_ok=True)
log_filename = os.path.join(LOG_FOLDER, f'package_scan_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)

def get_config():
    """Reads configuration from the script's directory."""
    config = {
        'excluded_folders': ['bin', 'config', 'defaultconfigs', 'shaderpacks', 'resourcepacks', 'journeymap'],
        'excluded_files': ['mergetool', 'minecraft.jar']
    }
    
    config_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), CONFIG_FILE_NAME)
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    try:
                        key, value = line.split('=', 1)
                        if key in ['excluded_folders', 'excluded_files']:
                            config[key] = [item.strip() for item in value.split(',')]
                    except ValueError:
                        logging.warning(f"Skipping invalid line in config: {line}")
    return config

def find_packages_in_archive(archive_path, excluded_files):
    """Extracts unique package names from a .jar or .zip file."""
    packages = set()
    base_name = os.path.basename(archive_path).lower()

    if any(excluded in base_name for excluded in excluded_files):
        logging.info(f"Skipping excluded file: {archive_path}")
        return set()

    try:
        with zipfile.ZipFile(archive_path, 'r') as archive:
            for item in archive.namelist():
                if item.endswith('.class') and '/' in item:
                    # Extract the package path by removing the class file name
                    package_path = item.rsplit('/', 1)[0]
                    # Convert path to package notation
                    package_name = package_path.replace('/', '.')
                    packages.add(package_name)
    except (zipfile.BadZipFile, RuntimeError) as e:
        logging.error(f"Could not process {archive_path}: {e}")
    
    return packages

def generate_reports(all_packages, output_dir):
    """Generates raw and wildcard package reports."""
    # --- Raw Report ---
    raw_report_path = os.path.join(output_dir, 'package_report_raw.txt')
    with open(raw_report_path, 'w') as f:
        f.write("# Raw Package Report\n")
        f.write(f"# Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total Unique Packages Found: {len(all_packages)}\n\n")
        for pkg in sorted(list(all_packages)):
            f.write(f"{pkg}\n")
    logging.info(f"Raw package report saved to: {raw_report_path}")

    # --- Wildcard Report ---
    wildcard_packages = set()
    for pkg in all_packages:
        # Get the top-level and second-level packages (e.g., com, com.google)
        parts = pkg.split('.')
        if len(parts) > 1:
            wildcard_packages.add(f"{parts[0]}.{parts[1]}.*")
        elif len(parts) == 1:
            wildcard_packages.add(f"{parts[0]}.*")

    wildcard_report_path = os.path.join(output_dir, 'package_report_wildcard.txt')
    with open(wildcard_report_path, 'w') as f:
        f.write("# Wildcard Package Report\n")
        f.write(f"# Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total Unique Wildcard Groups: {len(wildcard_packages)}\n\n")
        for pkg in sorted(list(wildcard_packages)):
            f.write(f"{pkg}\n")
    logging.info(f"Wildcard package report saved to: {wildcard_report_path}")


def main():
    """Main function to drive the package scanning process."""
    config = get_config()
    
    while True:
        root_dir = input("Enter the root folder path containing your mods (or 'exit' to quit): ").strip()

        if root_dir.lower() == 'exit':
            break

        if not os.path.isdir(root_dir):
            logging.error(f"Invalid directory: {root_dir}")
            continue

        all_packages = set()
        logging.info(f"Starting scan in: {root_dir}")

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Modify dirnames in-place to prevent os.walk from descending into excluded folders
            dirnames[:] = [d for d in dirnames if d.lower() not in config['excluded_folders']]

            for filename in filenames:
                if filename.lower().endswith(('.jar', '.zip')):
                    archive_path = os.path.join(dirpath, filename)
                    packages = find_packages_in_archive(archive_path, config['excluded_files'])
                    if packages:
                        all_packages.update(packages)

        if not all_packages:
            logging.warning("No packages were found. Check the directory and config.")
        else:
            logging.info(f"Scan complete. Found {len(all_packages)} unique packages.")
            generate_reports(all_packages, os.path.dirname(os.path.realpath(__file__)))
        
        print("\n--- Scan Finished ---\n")

if __name__ == "__main__":
    main()