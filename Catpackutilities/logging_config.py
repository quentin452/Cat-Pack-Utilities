import logging
import os
import zipfile
import atexit

def backup_log(log_file, backup_zip):
    with zipfile.ZipFile(backup_zip, 'a') as zipf:
        zipf.write(log_file, arcname=os.path.basename(log_file))

def configure_logging(log_file_path='app_logs.log', log_level=logging.DEBUG):
    log_folder = 'logs'
    os.makedirs(log_folder, exist_ok=True)

    backup_zip = os.path.join(log_folder, 'app_logs_backup.zip')
    log_files = [file for file in os.listdir(log_folder) if file.startswith('app_logs_') and file.endswith('.log')]
    
    if log_files:
        latest_log_index = max(int(file.split('_')[2].split('.')[0]) for file in log_files)
    else:
        latest_log_index = 0
    
    log_file = os.path.join(log_folder, f'app_logs_{latest_log_index + 1}.log')

    if os.path.exists(log_file):
        print(f"Log file {log_file} already exists.")
    else:
        open(log_file, 'w').close()

    def save_log():
        if os.path.exists(backup_zip):
            backup_log(log_file, backup_zip)
        else:
            with zipfile.ZipFile(backup_zip, 'w') as zipf:
                zipf.write(log_file, arcname=os.path.basename(log_file))

    atexit.register(save_log)

    logging.basicConfig(filename=log_file, level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)

    logging.getLogger('').addHandler(console)


if __name__ == '__main__':
    configure_logging()
    logging.info('Hello, this is an example log message!')
