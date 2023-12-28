import logging
import os
import shutil
import zipfile

def configure_logging(log_file_path='app_logs.log', log_level=logging.DEBUG):
    # Create 'logs' directory if it doesn't exist
    log_folder = 'logs'
    os.makedirs(log_folder, exist_ok=True)

    # Set up logging to a file
    log_file = os.path.join(log_folder, log_file_path)

    # Check if log file exists
    if os.path.exists(log_file):
        # If log file exists, create a backup zip file and empty the log
        backup_zip = os.path.join(log_folder, 'app_logs_backup.zip')

        # Check if the backup zip file exists, if not, create a new one
        if not os.path.exists(backup_zip):
            with zipfile.ZipFile(backup_zip, 'w') as zipf:
                zipf.write(log_file, arcname=os.path.basename(log_file))
        else:
            with zipfile.ZipFile(backup_zip, 'a') as zipf:
                zipf.write(log_file, arcname=os.path.basename(log_file))

        # Empty the log file
        open(log_file, 'w').close()

    logging.basicConfig(filename=log_file, level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')

    # Define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)

    # Set a format which is simpler for console use
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)

    # Add the handler to the root logger
    logging.getLogger('').addHandler(console)
