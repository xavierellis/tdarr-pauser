#!/usr/bin/env python3
"""
Tdarr Pauser

This script monitors active Jellyfin sessions and automatically pauses or resumes Tdarr transcode jobs.
When a user is actively watching a video (not paused), Tdarr processing is paused to avoid interrupting playback.
When no active video sessions are detected, Tdarr processing is resumed.

Environment Variables:
    JELLYFIN_URL: Jellyfin server URL (default: http://jellyfin:8096)
    JELLYFIN_API_KEY: Jellyfin API key
    TDARR_URL: Tdarr server URL (default: http://tdarr-server:8266)
    POLL_SEC: Polling interval in seconds (default: 10)
"""

import os
import time
import requests
import sys
from typing import Dict, Optional

JELLY_URL: str = os.getenv("JELLYFIN_URL", "http://jellyfin:8096")
JELLY_KEY: Optional[str] = os.getenv("JELLYFIN_API_KEY")
TDARR_URL: str = os.getenv("TDARR_URL", "http://tdarr-server:8266")
POLL_SEC: int = int(os.getenv("POLL_SEC", "10"))

headers: Dict[str, str] = {"X-Emby-Token": JELLY_KEY} if JELLY_KEY else {}


def jelly_active() -> int:
    """
    Checks for active (not paused) video sessions in Jellyfin.

    Returns:
        int: The number of active video sessions.
    """
    try:
        r = requests.get(f"{JELLY_URL}/Sessions", headers=headers, timeout=5)
        r.raise_for_status()
        sessions = r.json()

        # Debug to see what's coming back
        print(f"Found {len(sessions)} sessions")

        active_count = 0
        for s in sessions:
            client = s.get("Client", "Unknown")
            username = s.get("UserName", "Unknown")
            play_state = s.get("PlayState", {})
            is_paused = play_state.get("IsPaused")
            now_playing_item = s.get("NowPlayingItem", {})
            now_playing_media_type = now_playing_item.get("MediaType")

            print(
                f"Session: {username} on {client}, Paused: {is_paused}, NowPlayingMediaType: {now_playing_media_type}"
            )

            # A session is active if it's not paused AND it's a Video type
            if is_paused is False and now_playing_media_type == "Video":
                active_count += 1
                print(f"↑ Counted as active: {username} on {client}")

        return active_count
    except Exception as e:
        print("Jellyfin query error:", e, file=sys.stderr)
        return 0


def tdarr(action: str) -> None:
    """
    Sends a pause or resume command to Tdarr.

    Args:
        action (str): "Start" to resume or "Stop" to pause processing.
    """
    try:
        requests.post(
            f"{TDARR_URL}/api/v2/pauseProcessing",
            json={"processStatus": action},
            timeout=5,
        )
    except Exception as e:
        print("Tdarr API error:", e, file=sys.stderr)


def main() -> None:
    """
    Main loop that checks Jellyfin sessions and controls Tdarr accordingly.
    """
    prev_state: Optional[str] = None
    while True:
        playing = jelly_active()
        if playing and prev_state != "paused":
            print("Video playing → pausing Tdarr")
            tdarr("Stop")
            prev_state = "paused"
        elif not playing and prev_state != "running":
            print("No active video → resuming Tdarr")
            tdarr("Start")
            prev_state = "running"
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
