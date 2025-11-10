#!/usr/bin/env python3
"""
send_to_telegram.py

Send images and videos from a folder to a Telegram channel.

Usage:
    python send_to_telegram.py /path/to/folder @channelusername
    python send_to_telegram.py /path/to/folder -1001234567890 --as-document
"""

from pathlib import Path
import argparse
import requests
import json
import os
from typing import List, Tuple

# ---------------------- CONFIG ----------------------
BOT_TOKEN = "8513459376:AAEs1AnahBBTdXlDQUlSSYYhfTSQZ6J7YhI"
# ----------------------------------------------------

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".flv", ".3gp", ".ts"}


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def is_video(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTS


def get_media_files(folder: Path) -> List[Path]:
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and (is_image(p) or is_video(p))]
    return files


def _check_response(resp, context=""):
    try:
        j = resp.json()
    except ValueError:
        resp.raise_for_status()
        raise RuntimeError(f"Non-JSON response for {context}")
    if not j.get("ok"):
        raise RuntimeError(f"Telegram API error for {context}: {j}")
    return j


def send_photo(chat_id: str, file_path: Path, caption: str = None):
    url = f"{API_URL}/sendPhoto"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with open(file_path, "rb") as f:
        files = {"photo": f}
        r = requests.post(url, data=data, files=files)
    return _check_response(r, file_path.name)


def send_video(chat_id: str, file_path: Path, caption: str = None):
    url = f"{API_URL}/sendVideo"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with open(file_path, "rb") as f:
        files = {"video": f}
        r = requests.post(url, data=data, files=files)
    return _check_response(r, file_path.name)


def send_document(chat_id: str, file_path: Path, caption: str = None):
    url = f"{API_URL}/sendDocument"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
    with open(file_path, "rb") as f:
        files = {"document": f}
        r = requests.post(url, data=data, files=files)
    return _check_response(r, file_path.name)


def send_media_group(chat_id: str, paths: List[Path], as_document: bool = False):
    """
    Send up to 10 media items as a single album. Items may be photos or videos.
    If as_document is True, send everything as documents individually (media group with documents not supported),
    so this function will not attempt a media group in that mode.
    """
    if not paths:
        return None

    if as_document:
        # send individually as documents
        results = []
        for p in paths:
            res = send_document(chat_id, p, caption=p.name)
            results.append(res)
        return results

    url = f"{API_URL}/sendMediaGroup"
    media = []
    files = {}
    # prepare attachments attach://fileN for local uploads
    for i, p in enumerate(paths[:10]):
        attach_name = f"file{i}"
        mtype = "photo" if is_image(p) else "video"
        media_item = {"type": mtype, "media": f"attach://{attach_name}"}
        # include caption only on first item (clients usually show first)
        if i == 0:
            media_item["caption"] = p.name
        media.append(media_item)
        files[attach_name] = open(p, "rb")

    data = {"chat_id": chat_id, "media": json.dumps(media)}
    try:
        r = requests.post(url, data=data, files=files)
        return _check_response(r, "media_group")
    finally:
        for f in files.values():
            try:
                f.close()
            except Exception:
                pass


def send_single_by_type(chat_id: str, path: Path, as_document: bool = False):
    """
    Sends a single file using appropriate endpoint.
    If as_document True -> sendDocument
    """
    if as_document:
        return send_document(chat_id, path, caption=path.name)

    if is_image(path):
        return send_photo(chat_id, path, caption=path.name)
    if is_video(path):
        # try sendVideo; if it fails (e.g. file too big for sendVideo), fallback to sendDocument
        try:
            return send_video(chat_id, path, caption=path.name)
        except Exception as e:
            print(f"⚠️ sendVideo failed for {path.name}: {e}. Trying sendDocument...")
            return send_document(chat_id, path, caption=path.name)
    raise RuntimeError("Unsupported media type: " + str(path))


def chunk_list(items: List[Path], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def main():
    parser = argparse.ArgumentParser(description="Send images and videos from a folder to a Telegram channel.")
    parser.add_argument("folder", help="Path to folder containing media")
    parser.add_argument("chat_id", help="Channel chat id (e.g. -100123...) or @username")
    parser.add_argument("--as-document", action="store_true",
                        help="Send all files as documents (preserve original quality).")
    parser.add_argument("--no-album", action="store_true",
                        help="Disable grouping into albums; send each file individually.")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser()
    chat_id = args.chat_id

    if not folder.exists() or not folder.is_dir():
        print("❌ Provided folder is invalid.")
        return

    media_files = get_media_files(folder)
    if not media_files:
        print("⚠️ No supported image/video files found in folder.")
        return

    print(f"Found {len(media_files)} media files in {folder}.")
    # If no-album OR only one file in the folder, send individually
    if args.no_album or len(media_files) == 1:
        for p in media_files:
            try:
                send_single_by_type(chat_id, p, as_document=args.as_document)
                print(f"✅ Sent {p.name}")
            except Exception as e:
                print(f"❌ Failed to send {p.name}: {e}")
        print("Done.")
        return

    # Default behaviour: batch by 10 and attempt media groups when possible
    for batch in chunk_list(media_files, 10):
        # If batch contains more than 1 file AND not as_document, try media group
        if len(batch) > 1 and not args.as_document:
            try:
                send_media_group(chat_id, batch, as_document=False)
                print(f"✅ Sent album of {len(batch)} items (first: {batch[0].name})")
            except Exception as e:
                print(f"⚠️ Media group failed: {e}. Falling back to individual sends for this batch.")
                # fallback: send individually
                for p in batch:
                    try:
                        send_single_by_type(chat_id, p, as_document=args.as_document)
                        print(f"✅ Sent {p.name}")
                    except Exception as e2:
                        print(f"❌ Failed to send {p.name}: {e2}")
        else:
            # single item (or as_document requested): send individually
            for p in batch:
                try:
                    send_single_by_type(chat_id, p, as_document=args.as_document)
                    print(f"✅ Sent {p.name}")
                except Exception as e:
                    print(f"❌ Failed to send {p.name}: {e}")

    print("🎉 All done.")


if __name__ == "__main__":
    main()
