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
SLEEPER_SERVICE_ALIAS = "sleeper-service"
SLEEPER_SERVICE_DEST_DIR = "~/dotfiles/scripts/aw-data/"  # Ensure this folder exists on sleeper-service
TARGET_TZ = ZoneInfo(os.getenv("TARGET_TZ", "Asia/Singapore"))
WATCHER_BUCKET_PREFIXES = ("aw-watcher-window", "aw-watcher-web", "aw-watcher-afk")
# ---------------------


def count_events(data):
    return sum(len(events) for events in data.values())


def hostname_from_bucket_id(bucket_id):
    for prefix in ("aw-watcher-window_", "aw-watcher-afk_"):
        if bucket_id.startswith(prefix):
            return bucket_id[len(prefix) :]
    if bucket_id.startswith("aw-watcher-web"):
        parts = bucket_id.split("_", 1)
        if len(parts) == 2:
            return parts[1]
    return None


def export_hostname(data):
    hostnames = []
    for bucket_id, events in data.items():
        if not events:
            continue
        hostname = hostname_from_bucket_id(bucket_id)
        if hostname:
            hostnames.append(hostname)

    if not hostnames:
        for bucket_id in data.keys():
            hostname = hostname_from_bucket_id(bucket_id)
            if hostname:
                hostnames.append(hostname)

    if hostnames:
        return Counter(hostnames).most_common(1)[0][0]
    return None


def hostname_matches_current_machine(bucket_hostname, current_hostname):
    if not bucket_hostname or not current_hostname:
        return False

    def aliases(hostname):
        hostname = hostname.lower().strip()
        short_hostname = hostname.split(".", 1)[0]
        values = {hostname, short_hostname}

        stem, separator, suffix = short_hostname.rpartition("-")
        if separator and suffix.isdigit() and stem:
            values.add(stem)

        return values

    return bool(aliases(bucket_hostname) & aliases(current_hostname))


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
        start_of_day = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=TARGET_TZ
        )
        end_time = start_of_day + timedelta(days=1)

    params = {"start": start_of_day.isoformat(), "end": end_time.isoformat()}

    resp = requests.get(f"{base_url}/buckets")
    buckets = resp.json()

    target_data = {}

    watcher_bucket_ids = []
    for bucket_id, bucket in buckets.items():
        if bucket_id.startswith(WATCHER_BUCKET_PREFIXES):
            watcher_bucket_ids.append(bucket_id)

    def fetch_events(bucket_id):
        print(f"Fetching events for: {bucket_id}")
        return requests.get(
            f"{base_url}/buckets/{bucket_id}/events", params=params
        ).json()

    selected_bucket_ids = []
    for bucket_id in watcher_bucket_ids:
        # Filter 1: Only buckets for THIS computer (hostname check, case-insensitive)
        # Filter 2: Only the watchers we care about (Window, Web, AFK)
        bucket = buckets.get(bucket_id)
        metadata_hostname = None
        if isinstance(bucket, dict):
            metadata_hostname = bucket.get("hostname")
        bucket_id_hostname = hostname_from_bucket_id(bucket_id)

        candidate_hostnames = [metadata_hostname, bucket_id_hostname]
        is_this_host = any(
            hostname_matches_current_machine(candidate, hostname)
            for candidate in candidate_hostnames
        )
        if is_this_host:
            selected_bucket_ids.append(bucket_id)

    for bucket_id in selected_bucket_ids:
        # Structure: { "bucket_id": [event1, event2...] }
        target_data[bucket_id] = fetch_events(bucket_id)

    if any(target_data.values()):
        return target_data

    if target_data:
        print(
            "Selected hostname buckets were empty; checking all watcher buckets for non-empty data."
        )

    fallback_data = {}
    fetched_bucket_ids = set(target_data)
    for bucket_id in watcher_bucket_ids:
        if bucket_id in fetched_bucket_ids:
            continue
        events = fetch_events(bucket_id)
        if events:
            fallback_data[bucket_id] = events

    if fallback_data:
        return fallback_data

    return {}


def sync_to_sleeper_service(data, target_date=None):
    event_count = count_events(data)
    if event_count == 0:
        print(
            f"No ActivityWatch events found for {target_date or 'today'}; "
            "skipping sync."
        )
        return False

    hostname = export_hostname(data) or socket.gethostname()
    if target_date is None:
        date_str = datetime.now(TARGET_TZ).strftime("%Y-%m-%d")
    else:
        date_str = target_date.strftime("%Y-%m-%d")

    # Filename includes hostname so it won't overwrite data from other machines
    filename = f"aw_{hostname}_{date_str}.json"
    local_path = f"/tmp/{filename}"

    with open(local_path, "w") as f:
        json.dump(data, f)

    print(f"Pushing {filename} to {SLEEPER_SERVICE_ALIAS}...")

    # Using the SSH alias directly
    cmd = [
        "rsync",
        "-az",
        local_path,
        f"{SLEEPER_SERVICE_ALIAS}:{SLEEPER_SERVICE_DEST_DIR}",
    ]

    try:
        subprocess.run(cmd, check=True)
        print("Sync success.")
        subprocess.run(["rm", local_path])  # Cleanup
        return True
    except subprocess.CalledProcessError as e:
        print(f"Sync failed. Check SSH connection to '{SLEEPER_SERVICE_ALIAS}'.")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Push ActivityWatch data to sleeper-service")
    parser.add_argument(
        "date",
        nargs="?",
        help="Date to push (YYYY-MM-DD format). Defaults to today.",
    )
    parser.add_argument(
        "-y",
        "--yesterday",
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
    if count_events(data) > 0:
        sync_to_sleeper_service(data, target_date)
    else:
        print(f"No ActivityWatch events found for {target_date or 'today'}.")
