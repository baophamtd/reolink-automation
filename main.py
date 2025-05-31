import os
from dotenv import load_dotenv
import json
from datetime import datetime, timedelta
from reolinkapi import Camera
import urllib3
import requests
import boto3
from botocore.exceptions import BotoCoreError, ClientError
import argparse

# Load environment variables from .env file
load_dotenv()

# Reolink config
REOLINK_HOST = os.getenv('REOLINK_HOST')
REOLINK_USER = os.getenv('REOLINK_USER')
REOLINK_PASSWORD = os.getenv('REOLINK_PASSWORD')
REOLINK_CHANNEL = 2  # Channel 2 based on file naming convention (RecM02)

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
    # TODO: Implement Telegram notification
    pass

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

def fetch_motion_files(start_dt, end_dt):
    """
    Query available motion files from Reolink camera for the given time range on channel 0.
    Tries both streamtype 'main' (Clear) and 'sub' (Fluent).
    Returns a dict with results for each streamtype.
    """
    results = {}
    try:
        cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
        cam.login()
        for streamtype in ['main', 'sub']:
            motion_files = cam.get_motion_files(
                start=start_dt,
                end=end_dt,
                channel=0,
                streamtype=streamtype
            )
            print(f"Motion files from {start_dt} to {end_dt} | channel 0 | streamtype '{streamtype}': {motion_files}")
            results[streamtype] = motion_files
        if not any(results.values()):
            print("No motion files found for channel 0.")
    except Exception as e:
        print(f"Motion file search failed: {e}")
        return None
    return results

def process_date_range(start_date, end_date):
    """
    For each day in the range [start_date, end_date], process all time ranges from download_times.json.
    """
    current_date = start_date
    while current_date <= end_date:
        print(f"\nProcessing date: {current_date.strftime('%Y-%m-%d')}")
        time_ranges = []
        for start_time, end_time in get_download_time_ranges():
            start_dt = current_date.replace(hour=start_time.hour, minute=start_time.minute, second=0, microsecond=0)
            end_dt = current_date.replace(hour=end_time.hour, minute=end_time.minute, second=0, microsecond=0)
            time_ranges.append((start_dt, end_dt))
        print(f"Found {len(time_ranges)} time ranges to process for {current_date.strftime('%Y-%m-%d')}.")
        for start_dt, end_dt in time_ranges:
            print(f"\nProcessing time range: {start_dt} to {end_dt}")
            results = fetch_motion_files(start_dt, end_dt)
            if results:
                files_to_download = []
                for streamtype in ['main', 'sub']:
                    files = results.get(streamtype, [])
                    for file in files:
                        files_to_download.append((file['filename'], file['start']))
                if files_to_download:
                    cam = Camera(REOLINK_HOST, REOLINK_USER, REOLINK_PASSWORD, https=True, defer_login=True)
                    cam.login()
                    for filename, file_start_time in files_to_download:
                        output_filename = file_start_time.strftime("%Y-%m-%d %H-%M-%S") + ".mp4"
                        if os.path.exists(output_filename):
                            print(f"File {output_filename} already exists locally, skipping download.")
                            continue
                        print(f"Attempting to download: {filename} as {output_filename}")
                        success = cam.get_file(
                            filename=filename,
                            output_path=output_filename,
                            method="Playback"
                        )
                        if success:
                            print(f"Download complete: {output_filename}")
                            s3_url = upload_to_s3(output_filename, S3_BUCKET, AWS_DEFAULT_REGION)
                            if s3_url:
                                print(f"File available at: {s3_url}")
                                os.remove(output_filename)
                                print(f"Deleted local file: {output_filename}")
                            else:
                                print("Failed to upload to S3.")
                        else:
                            print("Download failed.")
                else:
                    print("No motion files found to download in this range.")
            else:
                print("No motion files found in this range.")
        current_date += timedelta(days=1)

def main():
    # TODO: Implement main job logic
    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download and upload Reolink motion files for a date range.")
    parser.add_argument('--start', type=str, help='Start date (YYYY-MM-DD)', required=False)
    parser.add_argument('--end', type=str, help='End date (YYYY-MM-DD)', required=False)
    args = parser.parse_args()

    if args.start and args.end:
        start_date = datetime.strptime(args.start, "%Y-%m-%d")
        end_date = datetime.strptime(args.end, "%Y-%m-%d")
        process_date_range(start_date, end_date)
    else:
        # Default: process today
        today = datetime.now()
        process_date_range(today, today)
    main() 