import os
from dotenv import load_dotenv
import json
from datetime import datetime
from reolinkapi import Camera
import urllib3
import requests
import argparse
from telegram import Bot
import asyncio
import time
import random
import signal
from local_storage import download_to_local_storage, local_file_exists, ensure_storage_directory, get_local_filepath, apply_nextcloud_permissions

# Configuration: Dynamic timeout and retry strategies (configurable via environment variables)
TIMEOUT_BASE_SECONDS = int(os.getenv('REOLINK_TIMEOUT_BASE', 1800))  # 30 min base
TIMEOUT_PER_FILE_SECONDS = int(os.getenv('REOLINK_TIMEOUT_PER_FILE', 120))  # 2 min per file
TIMEOUT_MAX_SECONDS = int(os.getenv('REOLINK_TIMEOUT_MAX', 14400))  # 4 hours max
MAX_RETRIES = int(os.getenv('REOLINK_MAX_RETRIES', 5))
RETRY_DELAY_BASE = int(os.getenv('REOLINK_RETRY_DELAY_BASE', 30))  # Base delay in seconds
RETRY_BACKOFF_MAX_SECONDS = int(os.getenv('REOLINK_RETRY_BACKOFF_MAX', 600))
RETRY_JITTER_MAX_SECONDS = int(os.getenv('REOLINK_RETRY_JITTER_MAX', 5))

def calculate_estimated_timeout(file_count):
    """
    Calculate estimated timeout needed based on number of files.
    Returns timeout in seconds.
    """
    estimated = TIMEOUT_BASE_SECONDS + (file_count * TIMEOUT_PER_FILE_SECONDS)
    return min(estimated, TIMEOUT_MAX_SECONDS)


def compute_retry_delay(base_delay, attempt):
    """Exponential backoff with jitter. attempt is zero-based."""
    exp_delay = min(base_delay * (2 ** attempt), RETRY_BACKOFF_MAX_SECONDS)
    jitter = random.uniform(0, RETRY_JITTER_MAX_SECONDS)
    return int(exp_delay + jitter)


def is_retryable_exception(exc):
    msg = str(exc).lower()
    retryable_markers = [
        '503',
        'temporarily unavailable',
        'timeout',
        'timed out',
        'connection reset',
        'connection refused',
        'max retries exceeded',
        'too many requests',
        '429',
    ]
    return any(marker in msg for marker in retryable_markers)

# WORKING DEBUG VERSION: Fetches and downloads all motion files for today (midnight to now) for all channels (0-3), 'main' stream only.
# Use this as a reference point for a known good state.

# Load environment variables from .env file
load_dotenv()

# Reolink config
REOLINK_HOST = os.getenv('REOLINK_HOST')
REOLINK_USER = os.getenv('REOLINK_USER')
REOLINK_PASSWORD = os.getenv('REOLINK_PASSWORD')
REOLINK_CLIENT = os.getenv('REOLINK_CLIENT', 'aio').strip().lower()  # aio | legacy
USE_AIO_CLIENT = REOLINK_CLIENT == 'aio'

# Local storage config - no AWS S3 needed

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Job lifecycle state (for robust terminal status signaling)
TERMINATION_SIGNAL = None
JOB_RUN_ID = None
TERMINAL_STATUS_SENT = False


def _termination_handler(signum, frame):
    global TERMINATION_SIGNAL
    TERMINATION_SIGNAL = signum
    raise KeyboardInterrupt(f"Termination signal received: {signum}")

def download_video(start_dt, end_dt):
    """
    Query available playback files from Reolink camera for the given time range.
    Tries both channel 0 and 1, and both streamtype 'main' (Clear) and 'sub' (Fluent).
    Prints results for each combination.
    """
    try:
        cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
        cam.login()
        found_any = False
        for channel in [0, 1]:
            for streamtype in ['main', 'sub']:
                files = cam.get_playback_files(
                    start=start_dt,
                    end=end_dt,
                    channel=channel,
                    streamtype=streamtype
                )
                print(f"Playback files from {start_dt} to {end_dt} | channel {channel} | streamtype '{streamtype}': {files}")
                if files:
                    found_any = True
        if not found_any:
            print("No playback files found for any channel/streamtype combination.")
    except Exception as e:
        print(f"Download failed: {e}")
        return None


async def _aio_download_file_to_local_storage(fname, output_filename, target_date, max_retries=5, retry_delay=30):
    """Download one VOD file using reolink-aio and save directly to local storage."""
    from reolink_aio.api import Host

    local_filepath = get_local_filepath(output_filename, target_date)
    if os.path.isfile(local_filepath):
        return local_filepath

    ensure_storage_directory(target_date)

    for attempt in range(max_retries):
        host = Host(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, use_https=None, port=None, stream='main', timeout=15)
        vod = None
        try:
            await host.login()
            await host.get_host_data()
            vod = await host.download_vod(fname, wanted_filename=output_filename)

            with open(local_filepath, 'wb') as f:
                while True:
                    chunk = await vod.stream.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)

            try:
                vod.close()
            except Exception:
                pass

            apply_nextcloud_permissions(local_filepath, is_directory=False)
            file_size = os.path.getsize(local_filepath)
            print(f"Successfully downloaded to local storage: {local_filepath} ({file_size} bytes)")
            return local_filepath
        except Exception as e:
            if attempt < max_retries - 1 and is_retryable_exception(e):
                delay = compute_retry_delay(retry_delay, attempt)
                print(f"AIO download error for {fname}: {e}. Retrying {attempt + 1}/{max_retries} in {delay}s...")
                await asyncio.sleep(delay)
            else:
                print(f"Failed to download {fname} after {attempt + 1}/{max_retries} attempts via reolink-aio: {e}")
                if not is_retryable_exception(e):
                    print("Non-retryable AIO error encountered, aborting retries for this file.")
                break
        finally:
            try:
                if vod is not None:
                    vod.close()
            except Exception:
                pass
            try:
                await host.logout()
            except Exception:
                pass

    return None


def download_motion_files(motions, max_retries=None, retry_delay=None):
    """
    Download motion files and save them to local storage.
    Only processes channel 0 files.
    Uses increased timeouts and retry delays for better reliability.
    Retry strategies are configurable via environment variables.
    """
    # Use configurable values if not provided
    if max_retries is None:
        max_retries = MAX_RETRIES
    if retry_delay is None:
        retry_delay = RETRY_DELAY_BASE

    total_files = len(motions)
    files_to_download = sum(1 for m in motions if not local_file_exists(
        m['start'].strftime("%Y-%m-%d %H-%M-%S") + "_ch0.mp4",
        m['start'].date()
    ))

    # Log estimated timeout
    if files_to_download > 0:
        estimated_timeout = calculate_estimated_timeout(files_to_download)
        timeout_minutes = estimated_timeout // 60
        print(f"Estimated processing time: ~{timeout_minutes} minutes for {files_to_download} files to download")

    processed_count = 0
    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    for idx, motion in enumerate(motions, 1):
        fname = motion['filename']
        mstart = motion['start']
        output_filename = mstart.strftime("%Y-%m-%d %H-%M-%S") + "_ch0.mp4"
        target_date = mstart.date()

        if local_file_exists(output_filename, target_date):
            skipped_count += 1
            processed_count += 1
            print(f"[{processed_count}/{total_files}] File {output_filename} already exists in local storage, skipping download.")
            continue

        print(f"[{processed_count + 1}/{total_files}] Downloading {fname} as {output_filename}")

        if USE_AIO_CLIENT:
            result = asyncio.run(
                _aio_download_file_to_local_storage(
                    fname=fname,
                    output_filename=output_filename,
                    target_date=target_date,
                    max_retries=max_retries,
                    retry_delay=retry_delay,
                )
            )
            processed_count += 1
            if result:
                downloaded_count += 1
                print(f"[{processed_count}/{total_files}] Successfully downloaded to local storage: {result}")
            else:
                failed_count += 1
                print(f"[{processed_count}/{total_files}] Failed to download {fname} via reolink-aio")
            continue

        attempt = 0
        while attempt < max_retries:
            cam = None
            try:
                # Create a new camera connection with increased timeout
                cam = Camera(
                    REOLINK_HOST,
                    REOLINK_USER,
                    REOLINK_PASSWORD,
                    https=True,
                    defer_login=True,
                    timeout=60  # Increase timeout to 60 seconds for large files
                )

                # Try to login with retries
                login_attempt = 0
                while login_attempt < 3:  # 3 login retries
                    try:
                        cam.login()
                        print(f"Login success - Download attempt {attempt + 1}/{max_retries}")
                        break
                    except Exception as e:
                        login_attempt += 1
                        if login_attempt < 3:
                            print(f"Login failed, retrying {login_attempt}/3...")
                            time.sleep(5)  # Short delay between login attempts
                        else:
                            raise Exception(f"Failed to login after 3 attempts: {e}")

                # Configure session for streaming
                session = requests.Session()
                session.verify = False
                session.timeout = (30, 180)  # 30s connect timeout, 180s read timeout (3 minutes for large files)
                cam._session = session

                # Download directly to local storage
                result = download_to_local_storage(cam, fname, output_filename, target_date, max_retries, retry_delay)
                if result:
                    downloaded_count += 1
                    processed_count += 1
                    print(f"[{processed_count}/{total_files}] Successfully downloaded to local storage: {result}")
                    if cam:
                        try:
                            cam.logout()
                        except:
                            pass
                    break  # Success, exit retry loop
                else:
                    # Download failed, increment attempt and retry
                    attempt += 1
                    if attempt < max_retries:
                        # Exponential backoff: 30s, 60s, 120s, 240s
                        delay = compute_retry_delay(retry_delay, attempt - 1)
                        print(f"Download to local storage failed. Retrying {attempt}/{max_retries} in {delay}s...")
                        time.sleep(delay)
                    else:
                        print(f"Failed to download {fname} after {max_retries} attempts.")
                        processed_count += 1
                        failed_count += 1
                    continue

            except requests.exceptions.ReadTimeout:
                attempt += 1
                if cam:
                    try:
                        cam.logout()
                    except:
                        pass
                if attempt < max_retries:
                    # Exponential backoff for timeouts
                    delay = compute_retry_delay(retry_delay, attempt - 1)
                    print(f"Timeout while downloading {fname}. Retrying {attempt}/{max_retries} in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed to download {fname} after {max_retries} attempts due to timeouts.")
                    processed_count += 1
                    failed_count += 1
                    continue

            except requests.exceptions.ConnectionError as e:
                attempt += 1
                if cam:
                    try:
                        cam.logout()
                    except:
                        pass
                if attempt < max_retries:
                    # Exponential backoff for connection errors
                    delay = compute_retry_delay(retry_delay, attempt - 1)
                    print(f"Connection error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed to download {fname} after {max_retries} attempts due to connection errors.")
                    processed_count += 1
                    failed_count += 1
                    continue

            except requests.exceptions.RequestException as e:
                attempt += 1
                if cam:
                    try:
                        cam.logout()
                    except:
                        pass
                if attempt < max_retries:
                    # Exponential backoff for network errors
                    delay = compute_retry_delay(retry_delay, attempt - 1)
                    print(f"Network error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed to download {fname} after {max_retries} attempts due to network errors.")
                    processed_count += 1
                    failed_count += 1
                    continue

            except Exception as e:
                attempt += 1
                if cam:
                    try:
                        cam.logout()
                    except:
                        pass
                if attempt < max_retries:
                    # Exponential backoff for unexpected errors
                    delay = compute_retry_delay(retry_delay, attempt - 1)
                    print(f"Error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed to download {fname} after {max_retries} attempts due to unexpected error.")
                    processed_count += 1
                    failed_count += 1
                    continue

    # Print summary
    print(f"\n=== Download Summary ===")
    print(f"Total files: {total_files}")
    print(f"Downloaded: {downloaded_count}")
    print(f"Skipped (already exist): {skipped_count}")
    print(f"Failed: {failed_count}")
    print(f"Processed: {processed_count}/{total_files}")
    if processed_count < total_files:
        remaining = total_files - processed_count
        print(f"Remaining: {remaining} files (will be processed on next run)")

    return {
        "total_files": total_files,
        "downloaded_count": downloaded_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "processed_count": processed_count,
    }

def send_telegram_message(message):
    async def _send():
        max_retries = 3
        for attempt in range(max_retries):
            try:
                bot = Bot(token=TELEGRAM_BOT_TOKEN)
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
                print("Telegram notification sent.")
                return True
            except Exception as e:
                print(f"Failed to send Telegram message (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)  # Wait 5 seconds before retry
        return False
    return asyncio.run(_send())


def send_terminal_status(message):
    global TERMINAL_STATUS_SENT
    TERMINAL_STATUS_SENT = True
    return send_telegram_message(message)

def get_download_time_ranges():
    """Load and parse download time ranges from download_times.json for today."""
    with open('download_times.json', 'r') as f:
        time_ranges = json.load(f)
    today = datetime.now().date()
    result = []
    for tr in time_ranges:
        start_dt = datetime.combine(today, datetime.strptime(tr['start'], '%H:%M').time())
        end_dt = datetime.combine(today, datetime.strptime(tr['end'], '%H:%M').time())
        result.append((start_dt, end_dt))
    return result

def fetch_motion_files(cam, start_dt, end_dt, channel):
    """
    Query available motion files from Reolink camera for the given time range on the specified channel.
    Tries both streamtype 'main' (Clear) and 'sub' (Fluent).
    Returns a dict with results for each streamtype.
    """
    results = {}
    try:
        for streamtype in ['main', 'sub']:
            motion_files = cam.get_motion_files(
                start=start_dt,
                end=end_dt,
                channel=channel,
                streamtype=streamtype
            )
            print(f"Motion files from {start_dt} to {end_dt} | channel {channel} | streamtype '{streamtype}': {motion_files}")
            results[streamtype] = motion_files
        if not any(results.values()):
            print(f"No motion files found for channel {channel}.")
    except Exception as e:
        print(f"Motion file search failed: {e}")
        return None
    return results

def process_date_range(start_date, end_date):
    """
    For each day in the range [start_date, end_date], fetch all motion files for the day (midnight to 23:59) for all channels, then filter by time windows before downloading.
    """
    from datetime import datetime as dt, time as dttime
    current_date = start_date
    while current_date <= end_date:
        start = dt.combine(current_date, dt.min.time())
        end = dt.combine(current_date, dttime(23, 59, 59))
        print(f"\nProcessing date: {current_date.strftime('%Y-%m-%d')}")
        print(f"Fetching all motion files for {start} to {end}")
        cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
        cam.login()
        all_motions = []
        for channel in [0, 1, 2, 3]:
            motions = cam.get_motion_files(start=start, end=end, streamtype='main', channel=channel)
            print(f"Channel {channel} motions: {motions}")
            for motion in motions:
                motion['channel'] = channel  # Tag channel for later
            all_motions += motions
        cam.logout()

        # Load time windows for the date
        with open('download_times.json', 'r') as f:
            time_ranges = json.load(f)
        window_ranges = []
        for tr in time_ranges:
            win_start = dt.combine(current_date, dt.strptime(tr['start'], '%H:%M').time())
            win_end = dt.combine(current_date, dt.strptime(tr['end'], '%H:%M').time())
            window_ranges.append((win_start, win_end))

        # Filter motions by time window
        filtered_motions = []
        for motion in all_motions:
            mstart = motion['start']
            for win_start, win_end in window_ranges:
                if win_start <= mstart < win_end:
                    filtered_motions.append(motion)
                    break

        print(f"Found {len(filtered_motions)} motion files in desired windows.")
        for motion in filtered_motions:
            fname = motion['filename']
            channel = motion.get('channel', 'unknown')
            mstart = motion['start']
            output_filename = mstart.strftime("%Y-%m-%d %H-%M-%S") + f"_ch{channel}.mp4"
            target_date = current_date  # for process_date_range
            if not os.path.isfile(output_filename):
                print(f"Downloading {fname} as {output_filename}")
                cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
                cam.login()
                resp = cam.get_file(fname, output_path=output_filename)
                cam.logout()
                print(f"Downloaded to {output_filename}")
                # Save to local storage
                from local_storage import save_to_local_storage
                result = save_to_local_storage(output_filename, target_date)
                if result:
                    print(f"Saved to local storage: {result}")
                else:
                    print("Failed to save to local storage.")
            else:
                print(f"File {output_filename} already exists, skipping download.")
        current_date += timedelta(days=1)

def process_date_with_window_filter(target_date):
    """
    Fetch all motion files for the given date (midnight to 23:59) for all channels, then filter by time windows before downloading.
    """
    from datetime import datetime as dt, time as dttime
    start = dt.combine(target_date, dt.min.time())
    end = dt.combine(target_date, dttime(23, 59, 59))
    print(f"\nProcessing date: {target_date.strftime('%Y-%m-%d')}")
    print(f"Fetching all motion files for {start} to {end}")
    cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
    cam.login()
    all_motions = []
    for channel in [0, 1, 2, 3]:
        motions = cam.get_motion_files(start=start, end=end, streamtype='main', channel=channel)
        print(f"Channel {channel} motions: {motions}")
        for motion in motions:
            motion['channel'] = channel  # Tag channel for later
        all_motions += motions
    cam.logout()

    # Load time windows for the date
    with open('download_times.json', 'r') as f:
        time_ranges = json.load(f)
    window_ranges = []
    for tr in time_ranges:
        win_start = dt.combine(target_date, dt.strptime(tr['start'], '%H:%M').time())
        win_end = dt.combine(target_date, dt.strptime(tr['end'], '%H:%M').time())
        window_ranges.append((win_start, win_end))

    # Filter motions by time window
    filtered_motions = []
    for motion in all_motions:
        mstart = motion['start']
        for win_start, win_end in window_ranges:
            if win_start <= mstart < win_end:
                filtered_motions.append(motion)
                break

    print(f"Found {len(filtered_motions)} motion files in desired windows.")
    for motion in filtered_motions:
        fname = motion['filename']
        channel = motion.get('channel', 'unknown')
        mstart = motion['start']
        output_filename = mstart.strftime("%Y-%m-%d %H-%M-%S") + f"_ch{channel}.mp4"
        target_date = target_date  # for process_date_with_window_filter
        if not os.path.isfile(output_filename):
            print(f"Downloading {fname} as {output_filename}")
            cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
            cam.login()
            resp = cam.get_file(fname, output_path=output_filename)
            cam.logout()
            print(f"Downloaded to {output_filename}")
            # Save to local storage
            from local_storage import save_to_local_storage
            result = save_to_local_storage(output_filename, target_date)
            if result:
                print(f"Saved to local storage: {result}")
            else:
                print("Failed to save to local storage.")
        else:
            print(f"File {output_filename} already exists, skipping download.")

def get_all_motion_files_for_date(target_date, max_retries=3, retry_delay=30):
    """
    Fetches all motion files for the given date with retry logic.
    Adds a delay for today's date to allow for camera indexing.
    Only checks channel 0.

    Returns: (motions_list, fetch_error)
      - fetch_error is None on success/no-data
      - fetch_error is a string when repeated API/transport errors occurred
    """
    all_motions = []
    last_error = None

    for attempt in range(max_retries):
        try:
            today = datetime.now().date()
            if target_date == today:
                print("Checking today's motions, adding delay for indexing...")
                time.sleep(10)

            start_dt = datetime.combine(target_date, datetime.min.time())
            end_dt = datetime.combine(target_date, datetime.max.time())

            if USE_AIO_CLIENT:
                from reolink_aio.api import Host

                async def _fetch_via_aio():
                    host = Host(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, use_https=None, port=None, stream='main', timeout=10)
                    try:
                        await host.login()
                        await host.get_host_data()
                        statuses, files = await host.request_vod_files(
                            channel=0,
                            start=start_dt,
                            end=end_dt,
                            status_only=False,
                            stream='main',
                        )
                        motions = []
                        for f in files:
                            motions.append({
                                'start': f.start_time.astimezone().replace(tzinfo=None),
                                'end': f.end_time.astimezone().replace(tzinfo=None),
                                'filename': f.file_name,
                                'channel': 0,
                            })
                        print(f"Channel 0 statuses: {statuses}")
                        print(f"Channel 0 motions: {motions}")
                        return motions
                    finally:
                        try:
                            await host.logout()
                        except Exception:
                            pass

                all_motions = asyncio.run(_fetch_via_aio())
            else:
                cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
                cam.login()
                print("Login success")
                try:
                    channel_motions = cam.get_motion_files(
                        start=start_dt,
                        end=end_dt,
                        streamtype='main',
                        channel=0
                    )
                    print(f"Channel 0 motions: {channel_motions}")
                    for motion in channel_motions:
                        motion['channel'] = 0
                    all_motions.extend(channel_motions)
                finally:
                    try:
                        cam.logout()
                    except Exception:
                        pass

            if all_motions:
                return all_motions, None

            print(f"No motions found on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                delay = compute_retry_delay(retry_delay, attempt)
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)

        except Exception as e:
            last_error = str(e)
            print(f"Error fetching motions on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1 and is_retryable_exception(e):
                delay = compute_retry_delay(retry_delay, attempt)
                print(f"Retrying in {delay} seconds...")
                time.sleep(delay)
            elif not is_retryable_exception(e):
                print("Non-retryable fetch error encountered, stopping retries.")
                break

    if last_error:
        print(f"Failed to fetch motions after {max_retries} attempts")
    return all_motions, last_error

def filter_motions_by_time_windows(motions, target_date, time_windows):
    """
    Filter a list of motion dicts to only those whose 'start' time falls within any of the specified time windows.
    time_windows: list of dicts with 'start' and 'end' in 'HH:MM' format.
    Only processes channel 0 motions.
    """
    from datetime import datetime as dt
    window_ranges = []
    for tr in time_windows:
        win_start = dt.combine(target_date, dt.strptime(tr['start'], '%H:%M').time())
        win_end = dt.combine(target_date, dt.strptime(tr['end'], '%H:%M').time())
        window_ranges.append((win_start, win_end))

    filtered = []
    for motion in motions:
        mstart = motion['start']
        for win_start, win_end in window_ranges:
            if win_start <= mstart < win_end:
                filtered.append(motion)
                break

    return filtered


if __name__ == "__main__":
    import argparse
    from datetime import datetime as dt, timedelta
    parser = argparse.ArgumentParser(description="Download and filter Reolink motion files by time windows.")
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)', required=False)
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)', required=False)
    args = parser.parse_args()

    # Load time windows from download_times.json
    with open('download_times.json', 'r') as f:
        time_windows = json.load(f)

    JOB_RUN_ID = dt.now().strftime("%Y%m%d-%H%M%S")
    TERMINAL_STATUS_SENT = False

    # Register termination handlers so interrupted runs still send terminal status.
    signal.signal(signal.SIGINT, _termination_handler)
    signal.signal(signal.SIGTERM, _termination_handler)
    signal.signal(signal.SIGHUP, _termination_handler)
    signal.signal(signal.SIGQUIT, _termination_handler)

    try:
        print(f"Reolink client mode: {REOLINK_CLIENT}")
        job_run_id = JOB_RUN_ID
        # Send start notification
        send_telegram_message(f"🎥 [STARTED] Reolink video processing (job={job_run_id}, client={REOLINK_CLIENT})")

        if args.start and args.end:
            start_date = dt.strptime(args.start, "%Y-%m-%d").date()
            end_date = dt.strptime(args.end, "%Y-%m-%d").date()
            if start_date == end_date:
                print(f"\nProcessing {start_date}")
                motions, fetch_error = get_all_motion_files_for_date(start_date)
                filtered = filter_motions_by_time_windows(motions, start_date, time_windows)
                count = len(filtered)
                print(f"Found {count} motion files in desired windows for {start_date}.")
                if count > 0:
                    send_telegram_message(f"📥 Processing {count} videos from {start_date}...")
                    summary = download_motion_files(filtered)
                    send_terminal_status(
                        f"✅ [COMPLETED] job={job_run_id} date={start_date} total={summary['total_files']} downloaded={summary['downloaded_count']} skipped={summary['skipped_count']} failed={summary['failed_count']} processed={summary['processed_count']}"
                    )
                else:
                    if fetch_error:
                        send_terminal_status(f"❌ [FAILED] job={job_run_id} date={start_date} fetch_error={fetch_error}")
                        raise RuntimeError(f"Motion fetch failed for {start_date}: {fetch_error}")
                    send_terminal_status(f"✅ [COMPLETED] job={job_run_id} date={start_date} no_new_videos_in_time_windows")
            else:
                current_date = start_date
                total_downloaded = 0
                total_skipped = 0
                total_failed = 0
                total_processed = 0
                failed_dates = []
                while current_date <= end_date:
                    print(f"\nProcessing {current_date}")
                    motions, fetch_error = get_all_motion_files_for_date(current_date)
                    filtered = filter_motions_by_time_windows(motions, current_date, time_windows)
                    count = len(filtered)
                    print(f"Found {count} motion files in desired windows for {current_date}.")
                    if count > 0:
                        send_telegram_message(f"📥 Processing {count} videos from {current_date}...")
                        summary = download_motion_files(filtered)
                        total_downloaded += summary['downloaded_count']
                        total_skipped += summary['skipped_count']
                        total_failed += summary['failed_count']
                        total_processed += summary['processed_count']
                    elif fetch_error:
                        failed_dates.append(f"{current_date}: {fetch_error}")
                    current_date += timedelta(days=1)

                if failed_dates:
                    send_terminal_status(
                        f"❌ [FAILED] job={job_run_id} range={start_date}->{end_date} errors={'; '.join(failed_dates)}"
                    )
                    raise RuntimeError(f"Fetch failures in range run: {'; '.join(failed_dates)}")

                if total_processed > 0:
                    send_terminal_status(
                        f"✅ [COMPLETED] job={job_run_id} range={start_date}->{end_date} downloaded={total_downloaded} skipped={total_skipped} failed={total_failed} processed={total_processed}"
                    )
                else:
                    send_terminal_status(
                        f"✅ [COMPLETED] job={job_run_id} range={start_date}->{end_date} no_new_videos_in_time_windows"
                    )
        elif args.start or args.end:
            print("Error: You must specify BOTH --start and --end to use date range mode.")
            exit(1)
        else:
            today = dt.now().date()
            print(f"\nProcessing {today}")
            motions, fetch_error = get_all_motion_files_for_date(today)
            filtered = filter_motions_by_time_windows(motions, today, time_windows)
            count = len(filtered)
            print(f"Found {count} motion files in desired windows for today.")
            if count > 0:
                send_telegram_message(f"📥 Processing {count} videos from today...")
                summary = download_motion_files(filtered)
                send_terminal_status(
                    f"✅ [COMPLETED] job={job_run_id} date={today} total={summary['total_files']} downloaded={summary['downloaded_count']} skipped={summary['skipped_count']} failed={summary['failed_count']} processed={summary['processed_count']}"
                )
            else:
                if fetch_error:
                    send_terminal_status(f"❌ [FAILED] job={job_run_id} date={today} fetch_error={fetch_error}")
                    raise RuntimeError(f"Motion fetch failed for {today}: {fetch_error}")
                send_terminal_status(f"✅ [COMPLETED] job={job_run_id} date={today} no_new_videos_in_time_windows")
    except KeyboardInterrupt as e:
        signal_info = f"signal={TERMINATION_SIGNAL}" if TERMINATION_SIGNAL else "signal=keyboard_interrupt"
        send_terminal_status(f"⚠ [ABORTED] job={JOB_RUN_ID} {signal_info} reason={e}")
        raise
    except Exception as e:
        send_terminal_status(f"❌ [FAILED] Reolink automation job failed: {e}")
        raise
    finally:
        # If the process exits without an explicit terminal status, send one to avoid ghost runs.
        if JOB_RUN_ID and not TERMINAL_STATUS_SENT:
            send_terminal_status(f"⚠ [ABORTED] job={JOB_RUN_ID} reason=process_exited_without_terminal_status")