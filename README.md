# Reolink Automation

This project automates downloading motion-triggered video clips from your Reolink camera (channel 0), uploads them to AWS S3, and cleans up local files. It is designed for easy scheduling (e.g., via cron) and works well on a Raspberry Pi.

## Features
- Download all motion-triggered video clips for channel 0 for a given date or date range
- Filter videos by configurable time windows (e.g., only download clips from 09:00–09:30, etc.)
- Upload each video to AWS S3
- Clean up local files after upload
- Easily configurable time windows and date ranges

## Setup

### 1. Clone the Repository
```bash
git clone https://github.com/baophamtd/reolink-automation.git
cd reolink-automation
```

### 2. Install Dependencies (Raspberry Pi example)
```bash
sudo apt update && sudo apt install python3-pip python3-dev ffmpeg -y
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Create a `.env` file in the project root (see `.env.example` for template):
```
REOLINK_HOST=192.168.1.100
REOLINK_USER=your_username
REOLINK_PASSWORD=your_password
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_DEFAULT_REGION=us-west-1
S3_BUCKET=your_s3_bucket_name
```

### 4. Configure Download Time Windows
Edit `download_times.json` to specify the time windows you want to process each day:
```json
[
  {"start": "09:00", "end": "09:30"},
  {"start": "12:00", "end": "12:30"},
  {"start": "15:00", "end": "15:30"},
  {"start": "18:00", "end": "18:30"}
]
```

## Usage

### Run for Today (default)
```bash
python main.py
```
- Fetches all motion files for today (channel 0), filters by your time windows, uploads to S3, and deletes local files.

### Run for a Specific Date Range
```bash
python main.py --start 2025-05-29 --end 2025-05-31
```
- Fetches all motion files for each day in the range (inclusive), filters by your time windows, uploads to S3, and deletes local files.
- **Note:** You must specify BOTH `--start` and `--end` if you want to use date range mode. If only one is provided, the script will exit with an error.
- If `--start` and `--end` are the same, the script will only fetch and filter once for that day.

### Example Cron Job (Run Every Day at 11:00pm)
1. Make the script executable:
   ```bash
   chmod +x /home/pi/reolink-automation/run_reolink_motion.sh
   ```
2. Edit your crontab with `crontab -e` and add:
   ```
   0 23 * * * /home/pi/reolink-automation/run_reolink_motion.sh
   ```

## Notes
- Make sure your `.env` file is not committed to version control.
- The script will skip files that already exist locally.
- If you want to backfill historical data, use the `--start` and `--end` arguments.
- Only channel 0 is processed. If you need other channels, modify the script accordingly.

---

**Happy automating!** 