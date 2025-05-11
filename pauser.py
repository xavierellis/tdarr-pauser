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
    LOG_LEVEL: Logging level (e.g., DEBUG, INFO, WARNING, ERROR, CRITICAL) (default: INFO)
"""

import os
import time
import requests
import sys
import logging
from typing import Dict, Optional

JELLY_URL: str = os.getenv("JELLYFIN_URL", "http://jellyfin:8096")
JELLY_KEY: Optional[str] = os.getenv("JELLYFIN_API_KEY")
TDARR_URL: str = os.getenv("TDARR_URL", "http://tdarr-server:8266")
POLL_SEC: int = int(os.getenv("POLL_SEC", "10"))
LOG_LEVEL_STR: str = os.getenv("LOG_LEVEL", "INFO").upper()

# Configure logging
LOG_LEVEL = getattr(logging, LOG_LEVEL_STR, logging.INFO)
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

headers: Dict[str, str] = {"X-Emby-Token": JELLY_KEY} if JELLY_KEY else {}


def _log_debug_request_exception_details(e: requests.exceptions.RequestException):
    """Helper to log request and response details from a RequestException if DEBUG is enabled."""
    if logger.isEnabledFor(logging.DEBUG):
        if e.request:
            logger.debug(f"Request URL: {e.request.url}")
            logger.debug(f"Request Headers: {e.request.headers}")
            if e.request.body:
                logger.debug(f"Request Body: {e.request.body}")
        if e.response is not None:
            logger.debug(f"Response Status Code: {e.response.status_code}")
            logger.debug(f"Response Headers: {e.response.headers}")
            logger.debug(f"Response Text: {e.response.text}")


def _log_debug_response_details(response: Optional[requests.Response]):
    """Helper to log response details if DEBUG is enabled."""
    if logger.isEnabledFor(logging.DEBUG) and response is not None:
        logger.debug(f"Response Status Code: {response.status_code}")
        logger.debug(f"Response Headers: {response.headers}")
        logger.debug(f"Response Text: {response.text}")


def jelly_active() -> int:
    """
    Checks for active (not paused) video sessions in Jellyfin.

    Returns:
        int: The number of active video sessions.
    """
    r = None  # Initialize r to None
    try:
        r = requests.get(f"{JELLY_URL}/Sessions", headers=headers, timeout=5)
        r.raise_for_status()
        sessions = r.json()

        logger.info(f"Found {len(sessions)} sessions")

        active_count = 0
        for s in sessions:
            client = s.get("Client", "Unknown")
            username = s.get("UserName", "Unknown")
            play_state = s.get("PlayState", {})
            is_paused = play_state.get("IsPaused")
            now_playing_item = s.get("NowPlayingItem", {})
            now_playing_media_type = now_playing_item.get("MediaType")

            logger.debug(
                f"Session: {username} on {client}, Paused: {is_paused}, NowPlayingMediaType: {now_playing_media_type}"
            )

            # A session is active if it's not paused AND it's a Video type
            if is_paused is False and now_playing_media_type == "Video":
                active_count += 1
                logger.debug(f"↑ Counted as active: {username} on {client}")

        return active_count
    except requests.exceptions.RequestException as e:
        logger.error(f"Jellyfin API request error: {e}", exc_info=True)
        _log_debug_request_exception_details(e)
        return 0
    except (requests.exceptions.JSONDecodeError, ValueError) as e:  # Handles JSON decoding errors
        logger.error(
            f"Jellyfin response JSON decoding error: {e}", exc_info=True)
        if logger.isEnabledFor(logging.DEBUG) and r is not None:
            logger.debug(f"Response Text (non-JSON): {r.text}")
        return 0
    except Exception as e:
        logger.error("Jellyfin query unexpected error", exc_info=True)
        return 0


def tdarr_toggle_nodes(pause: bool) -> None:
    """
    Toggles Tdarr’s global pauseAllNodes setting.
    pause=True  → pause all nodes
    pause=False → resume all nodes
    """
    body = {
        "data": {
            "collection": "SettingsGlobalJSONDB",
            "mode":       "update",
            "docID":      "globalsettings",
            "obj": {
                "pauseAllNodes": pause
            }
        },
        "timeout": 20000
    }
    try:
        response = requests.post(
            f"{TDARR_URL}/api/v2/cruddb",
            json=body,
            timeout=5
        )
        response.raise_for_status()
        action = "paused" if pause else "resumed"
        logging.info(f"Tdarr nodes successfully {action}.")
    except requests.exceptions.RequestException as e:
        logger.error(f"Tdarr API request error: {e}", exc_info=True)
        _log_debug_request_exception_details(e)
    except Exception as e:
        logger.error(f"Tdarr API unexpected error: {e}", exc_info=True)
        _log_debug_response_details(response)


def tdarr_cancel_active_workers() -> None:
    """
    Identifies and cancels active transcode tasks on all Tdarr nodes.
    This function assumes Tdarr nodes are already paused globally to prevent new tasks.
    """
    logger.info("Attempting to cancel active Tdarr worker tasks.")
    get_nodes_response: Optional[requests.Response] = None
    try:
        get_nodes_response = requests.get(
            f"{TDARR_URL}/api/v2/get-nodes", timeout=10)
        get_nodes_response.raise_for_status()
        nodes_data = get_nodes_response.json()
        _log_debug_response_details(get_nodes_response)

        cancelled_count = 0
        if not isinstance(nodes_data, dict):
            logger.warning(
                f"Unexpected format for get-nodes response. Expected a dictionary, got {type(nodes_data)}. Cannot process workers.")
            return

        for node_id, node_info in nodes_data.items():
            if not isinstance(node_info, dict):
                logger.warning(
                    f"Node data for '{node_id}' is not a dictionary. Skipping. Data: {node_info}")
                continue

            workers_obj = node_info.get('workers')
            if not isinstance(workers_obj, dict):
                logger.debug(
                    f"Node '{node_id}' has no 'workers' dictionary or it's not in the expected format. Skipping. Workers data: {workers_obj}")
                continue

            for worker_id, worker_details in workers_obj.items():
                if not isinstance(worker_details, dict):
                    logger.warning(
                        f"Worker details for '{worker_id}' on node '{node_id}' is not a dictionary. Skipping. Details: {worker_details}")
                    continue

                # Check if the worker is active by looking for a 'file' attribute.
                # The presence of a 'file' key (and it not being None) indicates it's working on something.
                # Also, worker_details.get('idle') == False could be an alternative or additional check.
                active_file = worker_details.get('file')
                is_active = active_file is not None

                if is_active:
                    logger.info(
                        f"Found active worker: Node ID '{node_id}', Worker ID '{worker_id}'. File: '{active_file}'. Attempting to cancel.")
                    cancel_payload = {
                        "data": {
                            "nodeID": node_id,
                            "workerID": worker_id,
                            "cause": "Paused by script due to Jellyfin activity"
                        }
                    }
                    cancel_response: Optional[requests.Response] = None
                    try:
                        cancel_response = requests.post(
                            f"{TDARR_URL}/api/v2/cancel-worker-item",
                            json=cancel_payload,
                            timeout=5
                        )
                        cancel_response.raise_for_status()
                        logger.info(
                            f"Successfully sent cancel request for worker '{worker_id}' on node '{node_id}'.")
                        _log_debug_response_details(cancel_response)
                        cancelled_count += 1
                    except requests.exceptions.RequestException as e_cancel:
                        logger.error(
                            f"Error cancelling worker '{worker_id}' on node '{node_id}': {e_cancel}")
                        _log_debug_request_exception_details(e_cancel)
                    except Exception as e_unexp_cancel:
                        logger.error(
                            f"Unexpected error cancelling worker '{worker_id}' on node '{node_id}': {e_unexp_cancel}", exc_info=True)
                        if cancel_response:
                            _log_debug_response_details(cancel_response)
                else:
                    logger.debug(
                        f"Worker '{worker_id}' on node '{node_id}' is not active (no 'file' attribute or 'file' is None). Skipping cancellation.")

        if cancelled_count > 0:
            logger.info(
                f"Attempted to cancel {cancelled_count} active Tdarr worker tasks.")
        else:
            logger.info(
                "No active Tdarr worker tasks found to cancel, or worker data structure was not as expected.")

    except requests.exceptions.RequestException as e:
        logger.error(
            f"Error fetching Tdarr nodes for worker cancellation: {e}")
        _log_debug_request_exception_details(e)
    except (requests.exceptions.JSONDecodeError, ValueError) as e_json:
        logger.error(f"Error decoding Tdarr nodes response: {e_json}")
        if get_nodes_response:
            logger.debug(
                f"Response Text (non-JSON from get-nodes): {get_nodes_response.text}")
    except Exception as e_unexp:
        logger.error(
            f"Unexpected error processing Tdarr nodes for worker cancellation: {e_unexp}", exc_info=True)


def main() -> None:
    """
    Main loop that checks Jellyfin sessions and controls Tdarr accordingly.
    """
    logger.info(
        f"Starting Tdarr Pauser. Polling every {POLL_SEC} seconds. Log level: {LOG_LEVEL_STR}")
    if not JELLY_KEY:
        logger.warning(
            "JELLYFIN_API_KEY is not set. This may cause issues if Jellyfin requires authentication.")

    prev_state: Optional[str] = None
    while True:
        playing = jelly_active()
        if playing > 0 and prev_state != "paused":
            logger.info(f"{playing} active video session(s) → pausing Tdarr")
            tdarr_toggle_nodes(pause=True)
            tdarr_cancel_active_workers()
            prev_state = "paused"
        elif playing == 0 and prev_state != "running":
            logger.info("No active video sessions → resuming Tdarr")
            tdarr_toggle_nodes(pause=False)
            # No need to uncancel workers here, as tdarr should pick them up automatically
            prev_state = "running"
        else:
            # Log current state if no change, useful for debugging or knowing it's still alive
            if playing > 0:
                logger.debug(
                    f"{playing} active video session(s). Tdarr remains paused.")
            else:
                logger.debug(
                    "No active video sessions. Tdarr remains running.")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
