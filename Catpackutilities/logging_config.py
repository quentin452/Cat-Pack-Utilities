import logging
import os
import zipfile


def backup_log(log_file, backup_zip):
    with zipfile.ZipFile(backup_zip, 'a') as zipf:
        zipf.write(log_file, arcname=os.path.basename(log_file))


def configure_logging(log_file_path='app_logs.log', log_level=logging.DEBUG):
    log_folder = 'logs'
    os.makedirs(log_folder, exist_ok=True)

    log_file = os.path.join(log_folder, log_file_path)

    if os.path.exists(log_file):
        backup_zip = os.path.join(log_folder, 'app_logs_backup.zip')

        if not os.path.exists(backup_zip):
            with zipfile.ZipFile(backup_zip, 'w') as zipf:
                zipf.write(log_file, arcname=os.path.basename(log_file))
        else:
            backup_log(log_file, backup_zip)

        open(log_file, 'w').close()

    logging.basicConfig(filename=log_file, level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)

    logging.getLogger('').addHandler(console)


if __name__ == '__main__':
    configure_logging()
    logging.info('Hello, this is an example log message!')
