import os
from datetime import datetime
import subprocess

# Local storage config - Nextcloud directory
LOCAL_STORAGE_PATH = "/mnt/data/nextcloud/data/bao/files/Photos/reolink-cams/e1"

def ensure_storage_directory(date=None):
    """
    Ensure the storage directory exists and create it if it doesn't.
    If date is provided, creates a subdirectory for that date.
    Sets proper permissions for Nextcloud access.
    """
    if date:
        # Create date-specific directory (YYYY-MM-DD format)
        date_str = date.strftime("%Y-%m-%d")
        storage_path = os.path.join(LOCAL_STORAGE_PATH, date_str)
    else:
        storage_path = LOCAL_STORAGE_PATH
    
    if not os.path.exists(storage_path):
        os.makedirs(storage_path, exist_ok=True)
        # Set proper permissions and ownership for Nextcloud
        os.chmod(storage_path, 0o775)  # Changed to 775 for group write access
        # Set ownership to current user and www-data group
        import subprocess
        try:
            import pwd
            current_user = pwd.getpwuid(os.getuid()).pw_name
            subprocess.run(['sudo', 'chown', f'{current_user}:www-data', storage_path], check=True)
            print(f"Set ownership to {current_user}:www-data for directory {storage_path}")
        except subprocess.CalledProcessError:
            print(f"Warning: Could not set ownership for {storage_path}")
            print("You may need to run: sudo usermod -aG www-data $USER")
        except FileNotFoundError:
            print(f"Warning: sudo not available, ownership not set for {storage_path}")
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
        
        # Set proper permissions and ownership for Nextcloud access
        os.chmod(destination_path, 0o644)
        # Set ownership to www-data (Nextcloud user)
        import subprocess
        try:
            subprocess.run(['sudo', 'chown', 'www-data:www-data', destination_path], check=True)
            print(f"Set ownership to www-data for {destination_path}")
        except subprocess.CalledProcessError:
            print(f"Warning: Could not set ownership for {destination_path}")
            print("You may need to run: sudo usermod -aG www-data $USER")
        except FileNotFoundError:
            print(f"Warning: sudo not available, ownership not set for {destination_path}")
        
        file_size = os.path.getsize(destination_path)
        print(f"Successfully saved to local storage: {destination_path} ({file_size} bytes)")
        
        # Trigger Nextcloud scan for the new file
        # trigger_nextcloud_scan(destination_path)  # Temporarily disabled for debugging
        
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
    
    # Ensure storage directory exists with proper permissions
    storage_path = ensure_storage_directory(date)
    
    # Double-check we can write to the directory
    if not os.access(storage_path, os.W_OK):
        print(f"ERROR: Cannot write to directory {storage_path}")
        print("Please run: sudo chown -R $USER:www-data /mnt/data/nextcloud/data/bao/files/Photos/reolink-cams/e1")
        print("And: sudo chmod -R 775 /mnt/data/nextcloud/data/bao/files/Photos/reolink-cams/e1")
        return None
    
    print(f"Downloading {fname} directly to {local_filepath}")
    print(f"Debug: Starting download attempt...")
    attempt = 0
    while attempt < max_retries:
        try:
            # Try to download with increased timeout
            resp = cam.get_file(fname, output_path=local_filepath)
            
            # If we get here, download was successful
            if os.path.isfile(local_filepath):
                # Set proper permissions and ownership for Nextcloud access
                os.chmod(local_filepath, 0o644)
                # Set ownership to www-data (Nextcloud user)
                import subprocess
                try:
                    subprocess.run(['sudo', 'chown', 'www-data:www-data', local_filepath], check=True)
                    print(f"Set ownership to www-data for {local_filepath}")
                except subprocess.CalledProcessError:
                    print(f"Warning: Could not set ownership for {local_filepath}")
                    print("You may need to run: sudo usermod -aG www-data $USER")
                except FileNotFoundError:
                    print(f"Warning: sudo not available, ownership not set for {local_filepath}")
                file_size = os.path.getsize(local_filepath)
                print(f"Successfully downloaded to local storage: {local_filepath} ({file_size} bytes)")
                
                # Trigger Nextcloud scan for the new file
                # trigger_nextcloud_scan(local_filepath)  # Temporarily disabled for debugging
                
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

def trigger_nextcloud_scan(filepath):
    """
    Trigger Nextcloud to scan for new files.
    Uses the Nextcloud occ command to scan the file.
    """
    try:
        # Get the relative path from the Nextcloud data directory
        nextcloud_data_path = "/mnt/data/nextcloud/data"
        if filepath.startswith(nextcloud_data_path):
            relative_path = filepath[len(nextcloud_data_path):].lstrip('/')
            # Extract the user and file path
            parts = relative_path.split('/', 2)  # user/files/path
            if len(parts) >= 3:
                user = parts[0]
                file_path = parts[2]  # Skip 'files' part
                
                # Run Nextcloud scan command
                cmd = [
                    'sudo', '-u', 'www-data', 
                    'docker', 'exec', 'nextcloud-nextcloud-1',
                    'php', 'occ', 'files:scan', '--path', f'{user}/files/{file_path}'
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    print(f"Nextcloud scan triggered for {file_path}")
                else:
                    print(f"Nextcloud scan failed: {result.stderr}")
            else:
                print(f"Could not parse file path for Nextcloud scan: {filepath}")
        else:
            print(f"File not in Nextcloud data directory: {filepath}")
            
    except subprocess.TimeoutExpired:
        print("Nextcloud scan timed out")
    except Exception as e:
        print(f"Failed to trigger Nextcloud scan: {e}")
        print("You may need to manually refresh Nextcloud or run: sudo -u www-data docker exec nextcloud-nextcloud-1 php occ files:scan --all") 