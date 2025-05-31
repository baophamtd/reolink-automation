# Reolink Automation

This project automates downloading video clips from Reolink cameras, uploads them to AWS S3, and sends Telegram notifications on completion. It is designed for easy scheduling (e.g., via cron) and works well on a Raspberry Pi.

## Features
- Download motion-triggered video clips from Reolink cameras for specific time windows
- Upload each video to AWS S3
- Clean up local files after upload
- Send a Telegram message on script completion (success or failure)
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
REOLINK_CHANNEL=0
AWS_ACCESS_KEY_ID=your_aws_access_key_id
AWS_SECRET_ACCESS_KEY=your_aws_secret_access_key
AWS_DEFAULT_REGION=us-west-1
S3_BUCKET=your_s3_bucket_name
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
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

### Run for a Specific Date Range
```bash
python main.py --start 2024-05-20 --end 2024-05-25
```

- The script will process all time windows in `download_times.json` for each day in the range.
- Each video will be uploaded to S3 and deleted locally after upload.
- You will receive a Telegram notification when the script finishes.

### Example Cron Job (Run Every Day at 11:00pm)
Edit your crontab with `crontab -e` and add:
```
0 23 * * * cd /home/pi/reolink-automation && /home/pi/reolink-automation/venv/bin/python main.py >> cron.log 2>&1
```

## Notes
- Make sure your `.env` file is not committed to version control.
- The script will skip files that already exist locally.
- If you want to backfill historical data, use the `--start` and `--end` arguments.

---

**Happy automating!** 