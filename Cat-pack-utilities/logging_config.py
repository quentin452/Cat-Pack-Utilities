import logging
import os


def configure_logging(log_file_path='app_logs.log', log_level=logging.DEBUG):
    # Create 'logs' directory if it doesn't exist
    log_folder = 'logs'
    os.makedirs(log_folder, exist_ok=True)

    # Set up logging to a file
    log_file = os.path.join(log_folder, log_file_path)
    logging.basicConfig(filename=log_file, level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')

    # Define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)

    # Set a format which is simpler for console use
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console.setFormatter(formatter)

    # Add the handler to the root logger
    logging.getLogger('').addHandler(console)


if __name__ == '__main__':
    # Configure logging when the file is run directly
    configure_logging()