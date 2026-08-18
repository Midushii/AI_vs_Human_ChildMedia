import yt_dlp
import csv
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# ============================
# PER-PERSON SETTINGS — CHANGE THIS FOR EACH PERSON
# ============================

PERSON_ID = 1     # <-- Person 1 = 0, Person 2 = 1, Person 3 = 2
TOTAL_PEOPLE = 3

# ============================
# SETTINGS
# ============================

BASE = Path("AI_Kids_Video_Dataset")
FOLDER = BASE / f"short_person{PERSON_ID}"
FOLDER.mkdir(parents=True, exist_ok=True)

FFMPEG = r"C:\Users\muskaan\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1.2-full_build\bin"

TARGET = 100   # each person aims for 100 -> 300 total combined
MIN_DURATION = 0
MAX_DURATION = 120

MAX_WORKERS = 6
MAX_RES = 480

DOWNLOAD_RETRIES = 3          # attempts per video before giving up on it
RETRY_BACKOFF_SECONDS = 3     # wait between retry attempts

MAX_SEARCH_CYCLES = 20        # safety cap so it can't loop forever if truly out of candidates
SEARCH_DEPTH_START = 50       # ytsearchN — grows each cycle to surface fresh results

# ============================
# SEARCH TERMS — split across people so search pools differ
# ============================

ALL_SEARCH_TERMS = [
    "AI animated kids story",
    "AI animal story animation",
    "AI cartoon moral story kids",
    "AI fantasy animation children",
    "AI generated cartoon movie",
    "kids bedtime story AI animation",
    "AI bunny story animation",
    "AI fairy tale cartoon",
    "AI emotional animal story",
    "AI princess story animation",
    "AI village story cartoon",
    "AI kids adventure animation",
    "AI animal friendship story",
    "AI cartoon short film",
]

SEARCH_TERMS = [
    term for i, term in enumerate(ALL_SEARCH_TERMS)
    if i % TOTAL_PEOPLE == PERSON_ID
]

BAD_WORDS = [
    "tutorial", "how to", "make", "create",
    "prompt", "tool", "course", "editing", "software",
]

AI_SIGNAL_WORDS = [
    "ai", "sora", "runway", "kling", "midjourney",
    "stable diffusion", "pika", "luma ai", "genmo",
    "text to video", "text-to-video", "ai generated",
    "ai-generated", "made with ai", "artificial intelligence",
]
AI_WORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in AI_SIGNAL_WORDS) + r")\b",
    re.IGNORECASE,
)

lock = threading.Lock()
total_downloaded = 0
seen_ids = set()
failed_ids = set()   # videos that permanently failed after all retries — never retry again


def log(*args):
    print(*args, flush=True)


def allowed(title):
    t = title.lower()
    return not any(bad in t for bad in BAD_WORDS)


def is_ai_generated(title, description):
    title = title or ""
    description = description or ""
    return bool(AI_WORD_PATTERN.search(title) or AI_WORD_PATTERN.search(description))


def in_duration_range(seconds):
    if seconds is None:
        return False
    return MIN_DURATION <= seconds <= MAX_DURATION


def existing_videos():
    ids = set()
    count = 0
    for f in FOLDER.glob("*.mp4"):
        count += 1
        ids.add(f.name[:11])
    return ids, count


def search_videos(term, depth):
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "ignoreerrors": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }

    results = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(f"ytsearch{depth}:" + term, download=False)
            if not data:
                return []

            for item in data.get("entries", []):
                if not item:
                    continue

                title = item.get("title", "")
                if not allowed(title):
                    continue

                vid_id = item.get("id")
                url = item.get("url") or item.get("webpage_url")
                if not vid_id or not url:
                    continue

                results.append({"id": vid_id, "title": title, "url": url})

    except Exception as e:
        log("SEARCH ERROR:", e)

    return results


def fetch_full_metadata(video):
    opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": False,
        "ignoreerrors": True,
        "noplaylist": True,
        "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video["url"], download=False)
            if not info:
                return None
            return {
                "id": info.get("id", video["id"]),
                "title": info.get("title", video["title"]),
                "description": info.get("description", "") or "",
                "duration": info.get("duration"),
                "url": info.get("webpage_url", video["url"]),
            }
    except Exception as e:
        log("METADATA FAILED:", video.get("title"), "-", e)
        return None


def download_once(video):
    """Single download attempt. Returns True only if an actual mp4 landed on disk."""
    output = str(FOLDER / "%(id)s_%(title)s.%(ext)s")

    opts = {
        "format": f"bestvideo[height<={MAX_RES}]+bestaudio/best[height<={MAX_RES}]",
        "outtmpl": output,
        "merge_output_format": "mp4",
        "ffmpeg_location": FFMPEG,
        "noplaylist": True,
        "ignoreerrors": True,
        "quiet": True,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 20,
        "keepvideo": False,   # FIX: force-delete separate video/audio source files after merge
    }

    try:
        before = set(FOLDER.glob("*"))

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([video["url"]])

        time.sleep(1)

        after = set(FOLDER.glob("*"))
        new_files = after - before
        mp4s = [f for f in new_files if f.suffix == ".mp4"]

        # FIX: clean up any leftover non-mp4 fragments for this video,
        # even when the mp4 itself succeeded (e.g. partial merge leftovers)
        leftovers = [f for f in new_files if f.suffix != ".mp4"]
        for f in leftovers:
            try:
                f.unlink()
                log(f"  Removed leftover fragment: {f.name}")
            except Exception:
                pass

        if not mp4s:
            return False

        if len(mp4s) > 1:
            # Rare, but if two mp4s somehow landed for one video,
            # keep only the first and remove the rest to avoid duplicates
            for extra in mp4s[1:]:
                try:
                    extra.unlink()
                    log(f"  Removed duplicate mp4: {extra.name}")
                except Exception:
                    pass

        return True

    except Exception as e:
        log("DOWNLOAD ATTEMPT FAILED:", video["title"], "-", e)
        for f in FOLDER.glob(video["id"] + "*"):
            try:
                f.unlink()
            except Exception:
                pass
        return False


def download_with_retries(video):
    """
    Keeps retrying THIS SPECIFIC video up to DOWNLOAD_RETRIES times
    before giving up on it. Only a confirmed successful download
    returns True. Nothing here touches the global counter — that only
    happens in process_candidate after this returns True.
    """
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        log(f"  Attempt {attempt}/{DOWNLOAD_RETRIES}: {video['title'][:60]}")

        if download_once(video):
            return True

        if attempt < DOWNLOAD_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS)

    log(f"  GIVING UP after {DOWNLOAD_RETRIES} failed attempts: {video['title'][:60]}")
    return False


def process_candidate(candidate):
    global total_downloaded

    with lock:
        if candidate["id"] in seen_ids or candidate["id"] in failed_ids:
            return
        if total_downloaded >= TARGET:
            return
        seen_ids.add(candidate["id"])

    video = fetch_full_metadata(candidate)
    if not video:
        log(f"  [dropped] metadata fetch failed/unavailable: {candidate['title'][:60]}")
        with lock:
            failed_ids.add(candidate["id"])
        return

    if not in_duration_range(video["duration"]):
        log(f"  [dropped] duration {video['duration']}s out of range ({MIN_DURATION}-{MAX_DURATION}s): {video['title'][:60]}")
        with lock:
            failed_ids.add(candidate["id"])
        return

    if not is_ai_generated(video["title"], video["description"]):
        log(f"  [dropped] no AI signal found: {video['title'][:60]}")
        with lock:
            failed_ids.add(candidate["id"])
        return

    with lock:
        if total_downloaded >= TARGET:
            return

    log("\n----------------")
    log(video["title"], "-", video["duration"], "sec")

    success = download_with_retries(video)

    if success:
        with lock:
            total_downloaded += 1
            current = total_downloaded
        log(f"SUCCESS: {current} / {TARGET}")
    else:
        with lock:
            failed_ids.add(video["id"])
        log("PERMANENTLY SKIPPED (did not count toward target)")


def main():
    global total_downloaded, seen_ids

    existing_ids, existing_count = existing_videos()
    seen_ids = set(existing_ids)
    total_downloaded = existing_count

    log(f"\nPERSON_ID = {PERSON_ID}")
    log("Assigned search terms:", SEARCH_TERMS)
    log("Existing:", existing_count, "| IDs:", len(existing_ids))

    cycle = 0
    search_depth = SEARCH_DEPTH_START

    while total_downloaded < TARGET and cycle < MAX_SEARCH_CYCLES:
        cycle += 1
        log(f"\n#################### CYCLE {cycle}/{MAX_SEARCH_CYCLES} ####################")
        log(f"Current total: {total_downloaded} / {TARGET}")

        any_new_candidates = False

        for term in SEARCH_TERMS:
            if total_downloaded >= TARGET:
                break

            log("\n====================")
            log("TOTAL:", total_downloaded, "/", TARGET)
            log("SEARCH:", term, f"(depth={search_depth})")

            candidates = search_videos(term, search_depth)

            # Only process candidates we haven't already seen/failed
            with lock:
                fresh_candidates = [
                    c for c in candidates
                    if c["id"] not in seen_ids and c["id"] not in failed_ids
                ]

            if fresh_candidates:
                any_new_candidates = True

            log(f"Found {len(candidates)} total, {len(fresh_candidates)} new/unfiltered candidates")

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [executor.submit(process_candidate, c) for c in fresh_candidates]
                for _ in as_completed(futures):
                    if total_downloaded >= TARGET:
                        break

        if total_downloaded >= TARGET:
            break

        if not any_new_candidates:
            # Widen the search depth so next cycle surfaces videos
            # beyond what we've already tried/rejected
            search_depth += 50
            log(f"\nNo new candidates this cycle — widening search depth to {search_depth}")

        time.sleep(2)  # brief pause between cycles to avoid hammering YouTube

    log("\n====================")
    if total_downloaded >= TARGET:
        log("FINISHED — TARGET REACHED")
    else:
        log(f"STOPPED — hit MAX_SEARCH_CYCLES ({MAX_SEARCH_CYCLES}) without reaching target")
        log("Consider adding more search terms or raising MAX_SEARCH_CYCLES / SEARCH_DEPTH_START")
    log("TOTAL DOWNLOADED:", total_downloaded, "/", TARGET)

    rows = [{"file": f.name, "category": f"short_person{PERSON_ID}"} for f in FOLDER.glob("*.mp4")]
    with open(BASE / f"metadata_person{PERSON_ID}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["file", "category"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()