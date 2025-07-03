import os
from datetime import datetime

# Local storage config
LOCAL_STORAGE_PATH = "/mnt/data/personal/reolink-cams/e1"

def ensure_storage_directory(date=None):
    """
    Ensure the storage directory exists and create it if it doesn't.
    If date is provided, creates a subdirectory for that date.
    """
    if date:
        # Create date-specific directory (YYYY-MM-DD format)
        date_str = date.strftime("%Y-%m-%d")
        storage_path = os.path.join(LOCAL_STORAGE_PATH, date_str)
    else:
        storage_path = LOCAL_STORAGE_PATH
    
    if not os.path.exists(storage_path):
        os.makedirs(storage_path, exist_ok=True)
        print(f"Created storage directory: {storage_path}")
    
    return storage_path

def save_to_local_storage(filepath, date=None):
    """
    Move a downloaded file to local storage.
    If date is provided, saves to a date-specific subdirectory.
    Returns the new filepath or None on failure.
    """
    try:
        if not os.path.isfile(filepath):
            print(f"Source file does not exist: {filepath}")
            return None
        
        # Get the filename
        filename = os.path.basename(filepath)
        
        # Ensure storage directory exists
        storage_path = ensure_storage_directory(date)
        
        # Create the destination path
        destination_path = os.path.join(storage_path, filename)
        
        # Check if file already exists
        if os.path.isfile(destination_path):
            print(f"File {filename} already exists in local storage, skipping.")
            # Remove the temporary file
            os.remove(filepath)
            return destination_path
        
        # Move the file to local storage
        import shutil
        shutil.move(filepath, destination_path)
        
        file_size = os.path.getsize(destination_path)
        print(f"Successfully saved to local storage: {destination_path} ({file_size} bytes)")
        
        return destination_path
        
    except Exception as e:
        print(f"Failed to save to local storage: {e}")
        return None

def local_file_exists(filename, date=None):
    """
    Check if a file already exists in local storage.
    If date is provided, checks in the date-specific subdirectory.
    """
    if date:
        date_str = date.strftime("%Y-%m-%d")
        filepath = os.path.join(LOCAL_STORAGE_PATH, date_str, filename)
    else:
        filepath = os.path.join(LOCAL_STORAGE_PATH, filename)
    
    return os.path.isfile(filepath)

def get_local_filepath(filename, date=None):
    """
    Get the full path where a file should be stored locally.
    If date is provided, returns path in date-specific subdirectory.
    """
    if date:
        date_str = date.strftime("%Y-%m-%d")
        return os.path.join(LOCAL_STORAGE_PATH, date_str, filename)
    else:
        return os.path.join(LOCAL_STORAGE_PATH, filename)

def download_to_local_storage(cam, fname, output_filename, date=None, max_retries=5, retry_delay=30):
    """
    Download a file directly to local storage.
    This replaces the download + upload pattern with direct local storage.
    """
    import time
    import requests
    
    # Get the destination path
    local_filepath = get_local_filepath(output_filename, date)
    
    # Check if file already exists
    if os.path.isfile(local_filepath):
        print(f"File {output_filename} already exists in local storage, skipping download.")
        return local_filepath
    
    # Ensure storage directory exists
    ensure_storage_directory(date)
    
    print(f"Downloading {fname} directly to {local_filepath}")
    attempt = 0
    while attempt < max_retries:
        try:
            # Try to download with increased timeout
            resp = cam.get_file(fname, output_path=local_filepath)
            
            # If we get here, download was successful
            if os.path.isfile(local_filepath):
                file_size = os.path.getsize(local_filepath)
                print(f"Successfully downloaded to local storage: {local_filepath} ({file_size} bytes)")
                return local_filepath
            else:
                raise Exception("Download completed but file not found")
                
        except requests.exceptions.ReadTimeout:
            attempt += 1
            if attempt < max_retries:
                print(f"Timeout while downloading {fname}. Retrying {attempt}/{max_retries} in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to download {fname} after {max_retries} attempts due to timeouts.")
                
        except requests.exceptions.RequestException as e:
            attempt += 1
            if attempt < max_retries:
                print(f"Network error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to download {fname} after {max_retries} attempts due to network errors.")
                
        except Exception as e:
            attempt += 1
            if attempt < max_retries:
                print(f"Error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to download {fname} after {max_retries} attempts due to unexpected error.")
    
    return None 