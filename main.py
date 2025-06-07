import os
from dotenv import load_dotenv
import json
from datetime import datetime
from reolinkapi import Camera
import urllib3
import requests
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import argparse
from telegram import Bot
import asyncio
import time

# WORKING DEBUG VERSION: Fetches and downloads all motion files for today (midnight to now) for all channels (0-3), 'main' stream only.
# Use this as a reference point for a known good state.

# Load environment variables from .env file
load_dotenv()

# Reolink config
REOLINK_HOST = os.getenv('REOLINK_HOST')
REOLINK_USER = os.getenv('REOLINK_USER')
REOLINK_PASSWORD = os.getenv('REOLINK_PASSWORD')

# AWS S3 config
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')
AWS_DEFAULT_REGION = os.getenv('AWS_DEFAULT_REGION')
S3_BUCKET = os.getenv('S3_BUCKET')

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def download_motion_files(start_dt, end_dt):
    """
    Query available motion files from Reolink camera for the given time range.
    Tries both channel 0 and 1, and both streamtype 'main' (Clear) and 'sub' (Fluent).
    Prints results for each combination.
    """
    try:
        cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
        cam.login()
        found_any = False
        for channel in [0, 1]:
            for streamtype in ['main', 'sub']:
                motion_files = cam.get_motion_files(
                    start=start_dt,
                    end=end_dt,
                    channel=channel,
                    streamtype=streamtype
                )
                print(f"Motion files from {start_dt} to {end_dt} | channel {channel} | streamtype '{streamtype}': {motion_files}")
                if motion_files:
                    found_any = True
        if not found_any:
            print("No motion files found for any channel/streamtype combination.")
    except Exception as e:
        print(f"Motion file search failed: {e}")
        return None

def upload_to_s3(filepath, bucket, aws_region=None):
    """
    Upload a file to AWS S3 and return the S3 URL or None on failure.
    """
    s3 = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=aws_region or AWS_DEFAULT_REGION
    )
    filename = os.path.basename(filepath)
    try:
        s3.upload_file(filepath, bucket, filename)
        s3_url = f"https://{bucket}.s3.{AWS_DEFAULT_REGION}.amazonaws.com/{filename}"
        print(f"Uploaded to S3: {s3_url}")
        return s3_url
    except (BotoCoreError, ClientError) as e:
        print(f"S3 upload failed: {e}")
        return None

def send_telegram_message(message):
    async def _send():
        try:
            bot = Bot(token=TELEGRAM_BOT_TOKEN)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)
            print("Telegram notification sent.")
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")
    asyncio.run(_send())

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
            if not os.path.isfile(output_filename):
                print(f"Downloading {fname} as {output_filename}")
                cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
                cam.login()
                resp = cam.get_file(fname, output_path=output_filename)
                cam.logout()
                print(f"Downloaded to {output_filename}")
                # Upload to S3
                s3_url = upload_to_s3(output_filename, S3_BUCKET, AWS_DEFAULT_REGION)
                if s3_url:
                    print(f"Uploaded to S3: {s3_url}")
                    os.remove(output_filename)
                    print(f"Deleted local file: {output_filename}")
                else:
                    print("Failed to upload to S3.")
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
        if not os.path.isfile(output_filename):
            print(f"Downloading {fname} as {output_filename}")
            cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
            cam.login()
            resp = cam.get_file(fname, output_path=output_filename)
            cam.logout()
            print(f"Downloaded to {output_filename}")
            # Upload to S3
            s3_url = upload_to_s3(output_filename, S3_BUCKET, AWS_DEFAULT_REGION)
            if s3_url:
                print(f"Uploaded to S3: {s3_url}")
                os.remove(output_filename)
                print(f"Deleted local file: {output_filename}")
            else:
                print("Failed to upload to S3.")
        else:
            print(f"File {output_filename} already exists, skipping download.")

def get_all_motion_files_for_date(target_date, max_retries=3, retry_delay=30):
    """
    Fetches all motion files for the given date with retry logic.
    Adds a delay for today's date to allow for camera indexing.
    """
    for attempt in range(max_retries):
        try:
            # For today's date, add a small delay to allow for indexing
            if target_date == datetime.now().date():
                time.sleep(10)  # Brief delay for indexing
            
            motions = []
            cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
            cam.login()
            for channel in [0, 1, 2, 3]:
                channel_motions = cam.get_motion_files(
                    start=datetime.combine(target_date, datetime.min.time()),
                    end=datetime.combine(target_date, datetime.max.time()),
                    streamtype='main',
                    channel=channel
                )
                print(f"Channel {channel} motions: {channel_motions}")
                for motion in channel_motions:
                    motion['channel'] = channel
                motions.extend(channel_motions)
            cam.logout()
            
            if not motions and attempt < max_retries - 1:
                print(f"No motions found on attempt {attempt + 1}, retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
                
            return motions
            
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Error fetching motions on attempt {attempt + 1}: {e}")
                print(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
            else:
                print(f"Failed to fetch motions after {max_retries} attempts: {e}")
                raise
    
    return []

def filter_motions_by_time_windows(motions, target_date, time_windows):
    """
    Filter a list of motion dicts to only those whose 'start' time falls within any of the specified time windows.
    time_windows: list of dicts with 'start' and 'end' in 'HH:MM' format.
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
        # Debug print for time types and values
        print(f"DEBUG: motion['start']: {mstart} (type: {type(mstart)})")
        for win_start, win_end in window_ranges:
            print(f"DEBUG: window: {win_start} to {win_end} (types: {type(win_start)}, {type(win_end)})")
            if win_start <= mstart < win_end:
                filtered.append(motion)
                break
    return filtered

def s3_file_exists(bucket, key, aws_region=None):
    s3 = boto3.client(
        's3',
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=aws_region or AWS_DEFAULT_REGION
    )
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            return False
        else:
            raise

def download_motion_files(motions, max_retries=3, retry_delay=5):
    for motion in motions:
        fname = motion['filename']
        channel = motion.get('channel', 'unknown')
        mstart = motion['start']
        output_filename = mstart.strftime("%Y-%m-%d %H-%M-%S") + f"_ch{channel}.mp4"
        s3_key = output_filename
        if s3_file_exists(S3_BUCKET, s3_key, AWS_DEFAULT_REGION):
            print(f"File {s3_key} already exists in S3, skipping download and upload.")
            continue
        if not os.path.isfile(output_filename):
            print(f"Downloading {fname} as {output_filename}")
            attempt = 0
            while attempt < max_retries:
                try:
                    cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
                    cam.login()
                    resp = cam.get_file(fname, output_path=output_filename)
                    cam.logout()
                    print(f"Downloaded to {output_filename}")
                    break  # Success, exit retry loop
                except requests.exceptions.ReadTimeout:
                    attempt += 1
                    print(f"Timeout while downloading {fname}. Retrying {attempt}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)
                except Exception as e:
                    attempt += 1
                    print(f"Error while downloading {fname}: {e}. Retrying {attempt}/{max_retries} in {retry_delay}s...")
                    time.sleep(retry_delay)
            else:
                print(f"Failed to download {fname} after {max_retries} attempts.")
                continue  # Skip upload if download failed
            # Upload to S3
            s3_url = upload_to_s3(output_filename, S3_BUCKET, AWS_DEFAULT_REGION)
            if s3_url:
                print(f"Uploaded to S3: {s3_url}")
                os.remove(output_filename)
                print(f"Deleted local file: {output_filename}")
            else:
                print("Failed to upload to S3.")
        else:
            print(f"File {output_filename} already exists locally, skipping download.")

def main():
    # TODO: Implement main job logic
    pass

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

    try:
        if args.start and args.end:
            start_date = dt.strptime(args.start, "%Y-%m-%d").date()
            end_date = dt.strptime(args.end, "%Y-%m-%d").date()
            if start_date == end_date:
                print(f"\nProcessing {start_date}")
                motions = get_all_motion_files_for_date(start_date)
                filtered = filter_motions_by_time_windows(motions, start_date, time_windows)
                print(f"Found {len(filtered)} motion files in desired windows for {start_date}.")
                download_motion_files(filtered)
            else:
                current_date = start_date
                while current_date <= end_date:
                    print(f"\nProcessing {current_date}")
                    motions = get_all_motion_files_for_date(current_date)
                    filtered = filter_motions_by_time_windows(motions, current_date, time_windows)
                    print(f"Found {len(filtered)} motion files in desired windows for {current_date}.")
                    download_motion_files(filtered)
                    current_date += timedelta(days=1)
        elif args.start or args.end:
            print("Error: You must specify BOTH --start and --end to use date range mode.")
            exit(1)
        else:
            today = dt.now().date()
            print(f"\nProcessing {today}")
            motions = get_all_motion_files_for_date(today)
            filtered = filter_motions_by_time_windows(motions, today, time_windows)
            print(f"Found {len(filtered)} motion files in desired windows for today.")
            download_motion_files(filtered)
        send_telegram_message("✅ Reolink automation script completed successfully.")
    except Exception as e:
        send_telegram_message(f"❌ Reolink automation script failed: {e}")
        raise
    main() 