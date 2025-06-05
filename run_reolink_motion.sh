#!/bin/bash
# Bash script to run the Reolink automation script from cron
# Usage: Add to crontab to run at desired times with redundancy

# Set the working directory to the project root
cd /home/baopham/reolink-automation

# Lock file to prevent simultaneous runs
LOCKFILE="/home/baopham/reolink_automation.lock"

# Check if another instance is running
if [ -f "$LOCKFILE" ]; then
    if kill -0 $(cat "$LOCKFILE") 2>/dev/null; then
        echo "$(date): Another instance is already running (PID: $(cat "$LOCKFILE")). Exiting."
        exit 1
    else
        echo "$(date): Removing stale lock file."
        rm -f "$LOCKFILE"
    fi
fi

# Create lock file with current PID
echo $$ > "$LOCKFILE"

# Cleanup function to remove lock file on exit
cleanup() {
    rm -f "$LOCKFILE"
}
trap cleanup EXIT

# Clear the log and start fresh
> cron.log

# Add timestamp
echo "=== Script started at $(date) ===" >> cron.log

# Activate the virtual environment
source venv/bin/activate

# Run the script and log output
python main.py >> cron.log 2>&1

echo "=== Script ended at $(date) ===" >> cron.log 