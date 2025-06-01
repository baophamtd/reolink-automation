#!/bin/bash
# Bash script to run the Reolink automation script from cron
# Usage: Add to crontab to run at desired time (e.g., 11pm every day)

# Set the working directory to the project root
cd /home/pi/reolink-automation

# Activate the virtual environment
source venv/bin/activate

# Run the script and log output
python main.py >> cron.log 2>&1 