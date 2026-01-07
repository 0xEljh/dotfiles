# /// script
# requires-python = ">3.11"
# dependencies= ["requests"]
# ///


import requests
import json
import subprocess
import socket
from datetime import datetime

# --- CONFIGURATION ---
VPS_ALIAS = "contabo"
VPS_DEST_DIR = "~/dotfiles/scripts/aw-data/"  # Ensure this folder exists on VPS
# ---------------------


def get_aw_data():
    base_url = "http://localhost:5600/api/0"
    hostname = socket.gethostname()

    # Range: Start of today (midnight local time) to now
    # This ensures we get the full picture of the current day every time we sync
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone()
    end_time = now.astimezone()

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

    for bucket_id in buckets.keys():
        bucket_id_lower = bucket_id.lower()
        # Filter 1: Only buckets for THIS computer (hostname check, case-insensitive)
        # Filter 2: Only the watchers we care about (Window, Web, AFK)
        is_this_host = (
            base_hostname in bucket_id_lower or "aw-watcher-web" in bucket_id_lower
        )
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


def sync_to_vps(data):
    hostname = socket.gethostname()
    date_str = datetime.now().strftime("%Y-%m-%d")

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
    data = get_aw_data()
    if data:
        sync_to_vps(data)
    else:
        print("No data found for today yet.")
