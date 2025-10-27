#!/bin/bash
# Bash script to run the Reolink automation script from cron
# Usage: Add to crontab to run at desired times with redundancy

# Set the working directory to the project root
cd /home/baopham/dev/reolink-automation

# Lock file to prevent simultaneous runs
LOCKFILE="/home/baopham/dev/reolink_automation.lock"

# Check if another instance is running
if [ -f "$LOCKFILE" ]; then
    PID=$(cat "$LOCKFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "$(date): Another instance is already running (PID: $PID). Exiting."
        exit 1
    else
        # Check if lock file is stale (older than 2 hours)
        LOCK_AGE=$(($(date +%s) - $(stat -c %Y "$LOCKFILE" 2>/dev/null || echo 0)))
        if [ $LOCK_AGE -gt 7200 ]; then  # 7200 seconds = 2 hours
            echo "$(date): Removing stale lock file (age: ${LOCK_AGE}s, PID: $PID)."
            rm -f "$LOCKFILE"
        else
            echo "$(date): Lock file exists but process not running (PID: $PID). Removing lock file."
            rm -f "$LOCKFILE"
        fi
    fi
fi

# Create lock file with current PID
echo $$ > "$LOCKFILE"

# Cleanup function to remove lock file on exit
cleanup() {
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

# Rotate log if it gets too large (> 10MB)
if [ -f cron.log ]; then
    LOG_SIZE=$(stat -c%s cron.log 2>/dev/null || echo 0)
    if [ $LOG_SIZE -gt 10485760 ]; then  # 10MB in bytes
        mv cron.log cron.log.old
        echo "$(date): Rotated large log file (${LOG_SIZE} bytes)" > cron.log
    fi
fi

# Append to log with timestamp (don't clear it)
echo "" >> cron.log
echo "============================================" >> cron.log
echo "=== Script started at $(date) ===" >> cron.log

# Activate the virtual environment
source venv/bin/activate

# Run the script and log output with timeout (1 hour max)
echo "=== Starting Python script with 1-hour timeout at $(date) ===" >> cron.log
timeout 3600 python main.py >> cron.log 2>&1
EXIT_CODE=$?

# Log timeout information
if [ $EXIT_CODE -eq 124 ]; then
    echo "=== Script timed out after 1 hour at $(date) ===" >> cron.log
    echo "$(date): Script timed out after 1 hour. Check for stuck downloads or network issues." >> cron.log
elif [ $EXIT_CODE -eq 0 ]; then
    echo "=== Script completed successfully at $(date) ===" >> cron.log
else
    echo "=== Script exited with code $EXIT_CODE at $(date) ===" >> cron.log
fi

# Trigger Nextcloud scan for just the e1 folder after all downloads are complete
echo "=== Triggering Nextcloud scan for e1 folder at $(date) ===" >> cron.log
docker exec nextcloud-nextcloud-1 php occ files:scan --path="bao/files/Photos/reolink-cams/e1" >> cron.log 2>&1

echo "=== Script ended at $(date) ===" >> cron.log 