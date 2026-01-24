# /// script
# requires-python = ">3.11"
# dependencies= ["requests"]
# ///


import argparse
import requests
import json
import subprocess
import socket
import os
from datetime import datetime, timedelta
from collections import Counter
from zoneinfo import ZoneInfo

# --- CONFIGURATION ---
VPS_ALIAS = "contabo"
VPS_DEST_DIR = "~/dotfiles/scripts/aw-data/"  # Ensure this folder exists on VPS
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore"))
# ---------------------


def get_aw_data(target_date=None):
    base_url = "http://localhost:5600/api/0"
    hostname = socket.gethostname()

    now = datetime.now(TARGET_TZ)

    if target_date is None:
        # Default: today, from midnight to now
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = now
    else:
        # Specific date: full day (midnight to midnight)
        start_of_day = datetime(target_date.year, target_date.month, target_date.day, tzinfo=TARGET_TZ)
        end_time = start_of_day + timedelta(days=1)

    params = {"start": start_of_day.isoformat(), "end": end_time.isoformat()}

    resp = requests.get(f"{base_url}/buckets")
    buckets = resp.json()

    target_data = {}

    # Get the short hostname (before first dot) for matching
    # This handles cases where bucket uses "Elijahs-MacBook-Air.local"
    # but socket.gethostname() returns "elijahs-macbook-air-2.tail82ff8b.ts.net"
    short_hostname = hostname.split(".")[0].lower()
    # Also extract the base name without trailing numbers (e.g., "elijahs-macbook-air" from "elijahs-macbook-air-2")
    base_hostname = short_hostname.rstrip("0123456789-")

    watcher_bucket_hostnames = []
    for bucket_id, bucket in buckets.items():
        if any(
            x in bucket_id
            for x in ["aw-watcher-window", "aw-watcher-web", "aw-watcher-afk"]
        ):
            if isinstance(bucket, dict):
                bucket_hostname = bucket.get("hostname")
                if bucket_hostname:
                    watcher_bucket_hostnames.append(bucket_hostname)

    aw_hostname = None
    if watcher_bucket_hostnames:
        for bucket_hostname in watcher_bucket_hostnames:
            if base_hostname and base_hostname in bucket_hostname.lower():
                aw_hostname = bucket_hostname
                break
        if aw_hostname is None:
            aw_hostname = Counter(watcher_bucket_hostnames).most_common(1)[0][0]

    for bucket_id in buckets.keys():
        bucket_id_lower = bucket_id.lower()
        # Filter 1: Only buckets for THIS computer (hostname check, case-insensitive)
        # Filter 2: Only the watchers we care about (Window, Web, AFK)
        bucket = buckets.get(bucket_id)
        bucket_hostname = None
        if isinstance(bucket, dict):
            bucket_hostname = bucket.get("hostname")

        is_this_host = False
        if aw_hostname and bucket_hostname:
            is_this_host = bucket_hostname == aw_hostname
        else:
            is_this_host = base_hostname in bucket_id_lower
        if is_this_host:
            if any(
                x in bucket_id
                for x in ["aw-watcher-window", "aw-watcher-web", "aw-watcher-afk"]
            ):
                print(f"Fetching events for: {bucket_id}")
                events = requests.get(
                    f"{base_url}/buckets/{bucket_id}/events", params=params
                ).json()

                # Structure: { "bucket_id": [event1, event2...] }
                target_data[bucket_id] = events

    return target_data


def sync_to_vps(data, target_date=None):
    hostname = socket.gethostname()
    if target_date is None:
        date_str = datetime.now(TARGET_TZ).strftime("%Y-%m-%d")
    else:
        date_str = target_date.strftime("%Y-%m-%d")

    # Filename includes hostname so it won't overwrite data from other machines
    filename = f"aw_{hostname}_{date_str}.json"
    local_path = f"/tmp/{filename}"

    with open(local_path, "w") as f:
        json.dump(data, f)

    print(f"Pushing {filename} to {VPS_ALIAS}...")

    # Using the SSH alias directly
    cmd = ["rsync", "-az", local_path, f"{VPS_ALIAS}:{VPS_DEST_DIR}"]

    try:
        subprocess.run(cmd, check=True)
        print("Sync success.")
        subprocess.run(["rm", local_path])  # Cleanup
    except subprocess.CalledProcessError as e:
        print(f"Sync failed. Check SSH connection to '{VPS_ALIAS}'.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push ActivityWatch data to VPS")
    parser.add_argument(
        "date",
        nargs="?",
        help="Date to push (YYYY-MM-DD format). Defaults to today.",
    )
    parser.add_argument(
        "-y", "--yesterday",
        action="store_true",
        help="Push yesterday's data",
    )
    args = parser.parse_args()

    target_date = None
    if args.yesterday:
        target_date = datetime.now(TARGET_TZ).date() - timedelta(days=1)
    elif args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    data = get_aw_data(target_date)
    if data:
        sync_to_vps(data, target_date)
    else:
        print(f"No data found for {target_date or 'today'}.")
