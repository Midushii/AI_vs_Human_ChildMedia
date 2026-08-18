"""
Fetch short (45-120s) ANIMATED, human-made kids' videos from a list of YouTube
channels using yt-dlp, split across 3 workers so each person downloads a
different, non-overlapping subset (no duplicates).

SETUP
------
pip install -U yt-dlp
Install ffmpeg and make sure it's on your PATH:
  - Mac:   brew install ffmpeg
  - Ubuntu: sudo apt install ffmpeg
  - Windows: download from https://ffmpeg.org/download.html and add to PATH

HOW THE 3-WAY SPLIT WORKS
--------------------------
1. The script builds one master list of candidate video IDs from all the
   channels below, then sorts it so the order is IDENTICAL on every machine.
2. Each worker only downloads videos where (index_in_list % 3 == WORKER_ID).
   Since all 3 people run the exact same CHANNELS list, the split is
   automatically non-overlapping -- no coordination or shared file needed.
3. Just change WORKER_ID to 0, 1, or 2 on each person's machine before running.

IMPORTANT: Explicitly excludes AI-generated content. This script does NOT
detect that automatically -- YOU must only add channels below that you've
manually verified are traditionally animated / human-made (no AI-generated
animation channels). yt-dlp has no reliable way to detect "AI-generated"
video content, so this is a manual curation step on your end.
"""

import os
import json
import yt_dlp

# ----------------------------------------------------------------------
# CONFIG -- edit these before running
# ----------------------------------------------------------------------

WORKER_ID = 0          # <-- CHANGE THIS PER PERSON: 0, 1, or 2. Nothing else.
TOTAL_WORKERS = 3
TARGET_PER_WORKER = 100   # 100 x 3 workers = 300 total videos

MIN_DURATION = 45      # seconds
MAX_DURATION = 120     # seconds

OUTPUT_DIR = f"downloads_worker_{WORKER_ID}"
PROGRESS_FILE = f"progress_worker_{WORKER_ID}.json"   # resume support

# Add the channel /videos URLs you've manually verified as traditionally
# animated, human-made kids content (NOT AI-generated).
CHANNELS = [
    "https://www.youtube.com/@CoComelon/videos",
    "https://www.youtube.com/@supersimplesongs/videos",
    "https://www.youtube.com/@Peekaboo_Kidz/videos",
    "https://www.youtube.com/@heyduggeeofficial/videos",
    "https://www.youtube.com/@DrPandaTV/videos",
    "https://www.youtube.com/@Numberblocks/videos",
    "https://www.youtube.com/@LittleBabyBum/videos",
    "https://www.youtube.com/@Pinkfong/videos",
    "https://www.youtube.com/@ChuChuTV/videos",
    "https://www.youtube.com/@SesameStreet/videos",
    "https://www.youtube.com/@babybus/videos",
    "https://www.youtube.com/@GoNoodle/videos",
    "https://www.youtube.com/@MashaBearEN/videos",
    "https://www.youtube.com/@BobtheBuilderOfficial/videos",
    "https://www.youtube.com/@PAWPatrolOfficial/videos",
    "https://www.youtube.com/@PeppaPigOfficial/videos",
    "https://www.youtube.com/@thomasandfriends/videos",
    # add more verified channels here -- double-check each one is
    # traditionally animated / human-made before adding, not AI-generated.
    #
    # NOTE: two channels from the earlier list were deliberately dropped
    # after verification -- see the chat notes for why:
    #   - "Kids Learning Tube" / "@StoryBotsShow" (StoryBots rebranded to
    #     "Netflix Jr.", @NetflixJr) -- add back manually if you still want it
    #   - "Wheels on the Bus" as a standalone brand doesn't map to one
    #     official channel; it's a generic song title reused by many
    #     unrelated/low-quality channels
]

# ----------------------------------------------------------------------
# STEP 1 -- Build the master (deterministic) video ID list
# ----------------------------------------------------------------------

def get_channel_video_ids(channel_url):
    """Flat, fast extraction -- just gets video IDs, no per-video metadata yet."""
    opts = {
        "extract_flat": "in_playlist",
        "quiet": True,
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        entries = info.get("entries", []) or []
        return [e["id"] for e in entries if e.get("id")]


def build_master_list():
    all_ids = []
    for ch in CHANNELS:
        print(f"[list] scanning {ch}")
        try:
            ids = get_channel_video_ids(ch)
            all_ids.extend(ids)
        except Exception as e:
            print(f"  !! failed to scan {ch}: {e}")
    # Dedup while preserving determinism, then sort so every worker's
    # machine produces the identical ordering.
    unique_sorted = sorted(set(all_ids))
    print(f"[list] {len(unique_sorted)} unique candidate videos found across all channels")
    return unique_sorted


# ----------------------------------------------------------------------
# STEP 2 -- Assign this worker's slice (no overlap between workers)
# ----------------------------------------------------------------------

def assign_to_worker(all_ids):
    return [vid for i, vid in enumerate(all_ids) if i % TOTAL_WORKERS == WORKER_ID]


# ----------------------------------------------------------------------
# STEP 3 -- Check duration + download only videos in the 45-120s window
# ----------------------------------------------------------------------

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f)
    return {"downloaded": [], "checked": []}


def save_progress(progress):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f, indent=2)


def get_duration(video_id):
    """Lightweight metadata-only fetch to check duration before downloading."""
    opts = {"quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        return info.get("duration")


def download_video(video_id):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/mp4",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(OUTPUT_DIR, "%(id)s - %(title).100s.%(ext)s"),
        "quiet": False,
        "noplaylist": True,
        # Only needed if ffmpeg isn't on PATH. Must be a raw string (r"...")
        # on Windows, otherwise backslashes get parsed as escape sequences.
        "ffmpeg_location": r"C:\Users\muskaan\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])


def main():
    print(f"=== Worker {WORKER_ID} of {TOTAL_WORKERS} (target: {TARGET_PER_WORKER} videos) ===")
    progress = load_progress()

    master_list = build_master_list()
    my_videos = assign_to_worker(master_list)
    print(f"[worker {WORKER_ID}] assigned {len(my_videos)} candidate videos to check")

    downloaded_count = len(progress["downloaded"])

    for vid in my_videos:
        if downloaded_count >= TARGET_PER_WORKER:
            print(f"[worker {WORKER_ID}] reached target of {TARGET_PER_WORKER}, stopping.")
            break
        if vid in progress["checked"]:
            continue  # already handled in a previous run

        try:
            duration = get_duration(vid)
        except Exception as e:
            print(f"  !! could not fetch metadata for {vid}: {e}")
            progress["checked"].append(vid)
            save_progress(progress)
            continue

        progress["checked"].append(vid)

        if duration is None or not (MIN_DURATION <= duration <= MAX_DURATION):
            save_progress(progress)
            continue

        print(f"  -> downloading {vid} ({duration}s)")
        try:
            download_video(vid)
            progress["downloaded"].append(vid)
            downloaded_count += 1
        except Exception as e:
            print(f"  !! download failed for {vid}: {e}")

        save_progress(progress)

    print(f"=== Worker {WORKER_ID} done: {downloaded_count} videos downloaded to '{OUTPUT_DIR}' ===")


if __name__ == "__main__":
    main()