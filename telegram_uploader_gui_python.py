"""
Telegram Uploader GUI

Save this file (for example) as: gui_instagram_uploader.py

What it does:
 - Lets you pick a folder containing images/videos
 - Enter Bot Token and Channel ID
 - Configure options: as-document, no-album, delay, jitter
 - Start / Stop uploads; shows progress, ETA estimate, and logs
 - Handles Telegram 429 (respects retry_after), exponential backoff, jitter
 - Persists progress to .upload_progress.json so you can resume

Requirements:
 - Python 3.8+
 - requests

Install requirements (recommended inside a venv):
    python3 -m venv venv
    source venv/bin/activate
    python -m pip install requests pillow tkinter

Run:
    python3 telegram_uploader_gui_python.py

"""

import os
import argparse
import json
import time
import random
import threading
import re
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import urllib.request
import urllib.error
import sys
import subprocess
import site
import importlib
import platform

try:
    import requests  # type: ignore
except ImportError as e:
    print(f"[WARN] 'requests' not installed or not importable: {e}")
    requests = None  # type: ignore
except Exception as e:  # unexpected import-time failure (shadowing, etc.)
    print(f"[WARN] 'requests' import failed unexpectedly: {e.__class__.__name__}: {e}")
    requests = None  # type: ignore

# Try import ThemedStyle if ttkthemes is already available; we'll also have a helper
try:
    from ttkthemes import ThemedStyle  # type: ignore
except Exception:
    ThemedStyle = None  # type: ignore

# ----------------- Appearance helpers (dark mode, tooltips) -----------------
def detect_dark_mode() -> bool:
    """Detect dark mode on macOS; fallback False on other OS or failure."""
    try:
        if platform.system() != 'Darwin':
            return False
        # Query AppleInterfaceStyle; presence of 'Dark' indicates dark mode
        out = subprocess.run(['defaults', 'read', '-g', 'AppleInterfaceStyle'],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return 'Dark' in (out.stdout or '')
    except Exception:
        return False

class Tooltip:
    def __init__(self, widget, text: str, delay: int = 400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule)
        widget.bind('<Leave>', self._hide)

    def _schedule(self, _event=None):
        self._unschedule()
        self._id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        if self._id:
            try:
                self.widget.after_cancel(self._id)
            except Exception:
                pass
            self._id = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 20
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            self._tip = tk.Toplevel(self.widget)
            self._tip.wm_overrideredirect(True)
            self._tip.attributes('-topmost', True)
            lbl = ttk.Label(self._tip, text=self.text, relief='solid', padding=(6, 3))
            lbl.pack()
            self._tip.wm_geometry(f"+{x}+{y}")
        except Exception:
            self._tip = None

    def _hide(self, _event=None):
        self._unschedule()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

# Supported extensions (used for logging and checks)
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}
VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}

# ----------------- Helper functions (networking & upload) -----------------

def is_image(p: Path) -> bool:
    return p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}


def is_video(p: Path) -> bool:
    return p.suffix.lower() in {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}

# ----------------- Dependency helper -----------------
def ensure_requests_available() -> bool:
    """Ensure the requests library is importable. Attempts auto-install if missing.
    Returns True if available, False otherwise."""
    global requests
    if requests is not None:
        return True
    # Try installing into user site-packages (PEP 668 friendly)
    try:
        print('[INFO] Attempting to auto-install requests to user site...')
        r = subprocess.run([sys.executable, '-m', 'pip', 'install', '--user', 'requests'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Ensure user site is on sys.path
        try:
            up = site.getusersitepackages()
            if up and up not in sys.path:
                sys.path.append(up)
        except Exception:
            pass
        requests = importlib.import_module('requests')  # type: ignore
        print('[INFO] Auto-installed requests in user site successfully.')
        return True
    except Exception as e:
        try:
            # Last attempt: try a normal install (may be blocked by PEP 668)
            print('[WARN] User-site install failed, attempting standard pip install (may be blocked by PEP 668)...')
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'requests'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            requests = importlib.import_module('requests')  # type: ignore
            print('[INFO] Auto-installed requests successfully.')
            return True
        except Exception as e2:
            print(f'[ERROR] Auto-install of requests failed: {e2}')
            return False

def ensure_ttkthemes_available() -> bool:
    """Ensure ttkthemes is importable. Attempts user-site install first.
    Returns True if available, False otherwise."""
    global ThemedStyle
    if ThemedStyle is not None:
        return True
    # Try user install
    try:
        print('[INFO] Attempting to auto-install ttkthemes to user site...')
        subprocess.run([sys.executable, '-m', 'pip', 'install', '--user', 'ttkthemes'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Ensure user site on sys.path
        try:
            up = site.getusersitepackages()
            if up and up not in sys.path:
                sys.path.append(up)
        except Exception:
            pass
        ThemedStyle = importlib.import_module('ttkthemes').ThemedStyle  # type: ignore
        print('[INFO] Auto-installed ttkthemes in user site successfully.')
        return True
    except Exception as e:
        try:
            print('[WARN] User-site install for ttkthemes failed, attempting standard pip install...')
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'ttkthemes'], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            ThemedStyle = importlib.import_module('ttkthemes').ThemedStyle  # type: ignore
            print('[INFO] Auto-installed ttkthemes successfully.')
            return True
        except Exception as e2:
            print(f'[ERROR] Auto-install of ttkthemes failed: {e2}')
            return False


class UploadWorker(threading.Thread):
    def __init__(self, folder: Path, token: str, chat_id: str, as_document: bool,
                 no_album: bool, delay: float, jitter: float, resume: bool,
                 delete_after_upload: bool, max_workers: int,
                 progress_callback, log_callback, done_callback, stop_event: threading.Event,
                 include_link: bool = True,
                 channel_link: str = '',
                 use_custom_caption: bool = False,
                 custom_caption: str = ''):
        super().__init__(daemon=True)
        self.folder = folder
        self.token = token.strip()
        self.chat_id = chat_id.strip()
        self.as_document = as_document
        self.no_album = no_album
        self.delay = max(0.0, float(delay))
        self.jitter = max(0.0, float(jitter))
        self.resume = resume
        self.delete_after_upload = delete_after_upload
        self.max_workers = max(1, min(10, int(max_workers)))  # Limit between 1-10 workers
        # Album/media group uploads are fragile when concurrent; force sequential to reduce failures.
        if not no_album and not as_document:
            self.max_workers = 1
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.done_callback = done_callback
        self.stop_event = stop_event
        self.rate_limit_lock = threading.Lock()
        self.rate_limit_dict = {}
        self.session = None  # Initialize session to None
        self.base_path = "/Users/udayaanudeep/Documents/Instagram/"
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.progress_file = self.folder / '.upload_progress.json'
        self.MAX_RETRIES = 6
        self.BASE_BACKOFF = 1.0
        # Caption link control (wired from GUI)
        self.include_link = bool(include_link)
        self.channel_link = channel_link.strip()
        self.use_custom_caption = bool(use_custom_caption)
        self.custom_caption = custom_caption.strip()

    def get_caption(self, file_path: Path) -> str:
        """
        Build the caption from the selected folder name, optionally appending
        a channel link if enabled in the UI.
        """
        try:
            # Determine base caption
            if self.use_custom_caption and self.custom_caption:
                base = self.custom_caption.strip()
            else:
                # Be robust whether self.folder is a Path or string
                try:
                    folder_name = Path(self.folder).name.strip() if self.folder else ''  # type: ignore[arg-type]
                except Exception:
                    folder_name = ''
                # Fallbacks: parent folder of file, then file stem
                if folder_name:
                    base = folder_name
                else:
                    try:
                        base = file_path.parent.name.strip() or file_path.stem
                    except Exception:
                        base = file_path.name
            if self.include_link and self.channel_link:
                cap = f"Instagram ID : {base}\n{self.channel_link}"
                # Keep within Telegram's typical 1024-char caption limit for media
                return cap[:1024]
            return f"Instagram ID : {base}"[:1024]
        except Exception:
            return file_path.name

    def log(self, *args):
        text = ' '.join(map(str, args))
        self.log_callback(text)

    def load_progress(self) -> List[str]:
        if not self.progress_file.exists():
            return []
        try:
            j = json.loads(self.progress_file.read_text())
            return j.get('uploaded', [])
        except Exception:
            return []

    def save_progress(self, uploaded: List[str]):
        try:
            self.progress_file.write_text(json.dumps({'uploaded': uploaded}))
        except Exception as e:
            self.log('Failed to write progress file:', e)

    def get_media_files(self) -> List[Path]:
        files = []
        for p in sorted(self.folder.iterdir()):
            if p.is_file() and (is_image(p) or is_video(p)):
                try:
                    # Check if file is accessible and not empty
                    if p.stat().st_size > 0:
                        files.append(p)
                    else:
                        self.log(f'⚠️ Skipping empty file: {p.name}')
                except Exception as e:
                    self.log(f'⚠️ Cannot access file {p.name}: {e}')
        return files  # Add this return statement

    def _parse_response_json(self, resp):
        try:
            return resp.json()
        except Exception:
            return None

    def init_session(self):
        """Initialize a new requests session"""
        if self.session:
            try:
                self.session.close()
            except:
                pass
        self.session = requests.Session()
        self.rate_limit_dict.clear()  # Clear any stored rate limiting state
        self.log('🔄 Reset connection and rate limit state')
        
    def request_with_retries(self, url: str, data=None, files=None, context=''):
        """Make HTTP request with retries and session management"""
        if not self.session:
            self.init_session()
            
        attempt = 0
        while attempt < self.MAX_RETRIES and not self.stop_event.is_set():
            attempt += 1
            # Prepare files_for_requests. If caller passed Paths (or strings),
            # open fresh file objects for this attempt so retries always send
            # a full body. For file-like objects, attempt to rewind to 0.
            files_for_requests = files
            opened_here = []
            try:
                if files:
                    # If files is dict and contains Path or str, open them per attempt
                    if isinstance(files, dict) and any(isinstance(v, (Path, str)) for v in files.values()):
                        files_for_requests = {}
                        for k, v in files.items():
                            p = Path(v)
                            try:
                                f = open(p, 'rb')
                                # requests accepts either fileobj or tuple (filename, fileobj)
                                files_for_requests[k] = (p.name, f)
                                opened_here.append(f)
                            except Exception as e:
                                self.log('Failed to open file for upload:', p, e)
                                # close any opened so far and raise
                                for of in opened_here:
                                    try:
                                        of.close()
                                    except Exception:
                                        pass
                                raise
                    else:
                        # best-effort rewind for provided file-like objects
                        try:
                            for k, v in (files.items() if isinstance(files, dict) else []):
                                fileobj = None
                                if hasattr(v, 'read') and hasattr(v, 'seek'):
                                    fileobj = v
                                elif isinstance(v, (list, tuple)) and len(v) >= 2 and hasattr(v[1], 'seek'):
                                    fileobj = v[1]
                                if fileobj:
                                    try:
                                        fileobj.seek(0)
                                    except Exception:
                                        pass
                        except Exception:
                            pass

            except Exception:
                # If preparing files failed, continue to next retry iteration
                for of in opened_here:
                    try:
                        of.close()
                    except Exception:
                        pass
                time.sleep(0.5)
                continue
            # If we're retrying a multipart upload we must ensure file-like
            # objects are rewound to the start before each attempt. Otherwise
            # subsequent attempts will send empty bodies which Telegram
            # reports as "file must be non-empty".
            if files:
                try:
                    for k, v in (files.items() if isinstance(files, dict) else []):
                        # requests accepts either file-like or tuples (filename, fileobj, content_type)
                        fileobj = None
                        if hasattr(v, 'read') and hasattr(v, 'seek'):
                            fileobj = v
                        elif isinstance(v, (list, tuple)) and len(v) >= 2 and hasattr(v[1], 'seek'):
                            fileobj = v[1]
                        if fileobj:
                            try:
                                fileobj.seek(0)
                            except Exception:
                                # best-effort rewind; ignore failures
                                pass
                except Exception:
                    # don't let debugging/retrying logic crash the upload loop
                    pass
            # Debug: log attempt and files info
            try:
                if isinstance(files, dict):
                    info = []
                    for k, v in files.items():
                        if isinstance(v, (Path, str)):
                            p = Path(v)
                            try:
                                size = p.stat().st_size
                            except Exception:
                                size = None
                            info.append(f"{k}={p.name}({size})")
                        else:
                            # file-like or tuple; try to get name
                            name = getattr(v, 'name', None)
                            if not name and isinstance(v, (list, tuple)) and len(v) >= 2:
                                name = getattr(v[1], 'name', None)
                            info.append(f"{k}={name}")
                    self.log(f'Attempt {attempt}/{self.MAX_RETRIES} for {context} files: {info}')
            except Exception:
                pass

            try:
                resp = self.session.post(url, data=data, files=files_for_requests, timeout=120)
            except requests.RequestException as e:
                backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                self.log(f'Network error ({context}) attempt {attempt}/{self.MAX_RETRIES}:', e, f'backoff={backoff:.1f}s')
                for of in opened_here:
                    try:
                        of.close()
                    except Exception:
                        pass
                time.sleep(backoff)
                continue

            j = self._parse_response_json(resp)
            if j is not None and not j.get('ok', False):
                code = j.get('error_code')
                params = j.get('parameters') or {}
                if code == 429:
                    retry_after = params.get('retry_after')
                    if retry_after is None:
                        backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                        self.log(f'429 (no retry_after) for {context}. Sleeping {backoff:.1f}s')
                        time.sleep(backoff)
                    else:
                        wait = int(retry_after) + 1
                        self.log(f'429 Too Many Requests for {context}. retry_after={retry_after}s — sleeping {wait}s')
                        # sleep while checking stop_event
                        for _ in range(wait):
                            if self.stop_event.is_set():
                                break
                            time.sleep(1)
                    continue
                # other API error
                self.log(f'Telegram API error for {context}:', j)
                backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                self.log(f'Backing off {backoff:.1f}s and retrying...')
                time.sleep(backoff)
                continue

            # close any file handles opened for this attempt
            try:
                for of in opened_here:
                    try:
                        of.close()
                    except Exception:
                        pass
            except Exception:
                pass

            return j if j is not None else resp

        raise RuntimeError(f'Failed after {self.MAX_RETRIES} attempts for {context}')

    def send_media_group(self, chat_id: str, paths: List[Path]):
        """Send up to 10 media items as a Telegram album (media group).
        Returns a tuple: (api_response, List[Path] actually processed).
        """
        if not paths:
            return None, []
        
        # Filter out any empty or inaccessible files
        valid_paths = []
        for p in paths:
            try:
                if p.stat().st_size > 0:
                    valid_paths.append(p)
                else:
                    self.log(f'⚠️ Skipping empty file in batch: {p.name}')
            except Exception as e:
                self.log(f'⚠️ Cannot access file in batch {p.name}: {e}')

        if not valid_paths:
            self.log('❌ No valid files in batch to send')
            return None, []

        if self.as_document:
            # documents cannot be sent in media group reliably
            results = []
            for p in valid_paths:
                r = self.send_single_by_type(chat_id, p)
                results.append(r)
                self._post_upload_action(p)
                if self.stop_event.is_set():
                    break
                time.sleep(self.delay + random.uniform(0, self.jitter))
            return results, valid_paths

        url = f"{self.api_url}/sendMediaGroup"
        media = []
        files = {}
        # Use the validated paths list when building the media and files dict
        for i, p in enumerate(valid_paths[:10]):
            attach = f'file{i}'
            mtype = 'photo' if is_image(p) else 'video'
            item = {'type': mtype, 'media': f'attach://{attach}'}
            caption = self.get_caption(p)
            if i == 0:
                item['caption'] = caption
            media.append(item)
            try:
                files[attach] = open(p, 'rb')
            except Exception as e:
                self.log('Failed to open', p, e)
                # close any opened
                for f in files.values():
                    try:
                        f.close()
                    except:
                        pass
                raise

        data = {'chat_id': chat_id, 'media': json.dumps(media)}
        try:
            j = self.request_with_retries(url, data=data, files=files, context=f'media_group {valid_paths[0].name}')
            return j, valid_paths
        finally:
            for f in files.values():
                try:
                    f.close()
                except:
                    pass

    def send_single_by_type(self, chat_id: str, path: Path):
        try:
            # Check file size before attempting to send
            if path.stat().st_size == 0:
                self.log(f'❌ Cannot send empty file: {path.name}')
                return None
                
            if self.as_document:
                return self.send_document(chat_id, path)
            if is_image(path):
                return self.send_photo(chat_id, path)
            if is_video(path):
                try:
                    return self.send_video(chat_id, path)
                except Exception as e:
                    self.log('sendVideo failed, falling back to document for', path.name, e)
                    return self.send_document(chat_id, path)
            raise RuntimeError('Unsupported media type: ' + str(path))
        except Exception as e:
            self.log(f'❌ Error checking file {path.name}: {e}')
            return None

    def send_photo(self, chat_id: str, file_path: Path):
        url = f"{self.api_url}/sendPhoto"
        data = {'chat_id': chat_id, 'caption': self.get_caption(file_path)}
        with open(file_path, 'rb') as f:
            files = {'photo': f}
            return self.request_with_retries(url, data=data, files=files, context=file_path.name)

    def send_video(self, chat_id: str, file_path: Path):
        url = f"{self.api_url}/sendVideo"
        data = {'chat_id': chat_id, 'caption': self.get_caption(file_path)}
        with open(file_path, 'rb') as f:
            files = {'video': f}
            return self.request_with_retries(url, data=data, files=files, context=file_path.name)

    def send_document(self, chat_id: str, file_path: Path):
        url = f"{self.api_url}/sendDocument"
        data = {'chat_id': chat_id, 'caption': self.get_caption(file_path)}
        with open(file_path, 'rb') as f:
            files = {'document': f}
            return self.request_with_retries(url, data=data, files=files, context=file_path.name)

    def _post_upload_action(self, p: Path):
        """After a successful upload, either move the file to .uploaded (safer) or delete it.
        This centralizes post-upload behavior so it's consistent across album/individual flows.
        """
        if not self.delete_after_upload:
            return
        try:
            target_dir = p.parent / '.uploaded'
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / p.name
            # avoid clobbering existing files
            if target.exists():
                base = p.stem
                suff = p.suffix
                i = 1
                while True:
                    candidate = target_dir / f"{base}_{i}{suff}"
                    if not candidate.exists():
                        target = candidate
                        break
                    i += 1
            p.replace(target)
            self.log(f'🗑️ Moved {p.name} -> {target.relative_to(p.parent)}')
        except Exception as e:
            # fallback to delete if move fails
            try:
                p.unlink()
                self.log(f'🗑️ Deleted {p.name} (fallback after move failure)')
            except Exception as e2:
                self.log(f'⚠️ Failed to remove {p.name}: {e} / {e2}')

    def run(self):
        if requests is None:
            self.log('The requests library is not installed. Please install requests and retry.')
            self.done_callback(False)
            return

        try:
            self.log(f'Scanning folder: {self.folder}')
            all_media = self.get_media_files()
            if not all_media:
                self.log('No supported media files found in folder.')
                try:
                    self.log(f'Supported image types: {", ".join(sorted(IMAGE_EXTS))}')
                    self.log(f'Supported video types: {", ".join(sorted(VIDEO_EXTS))}')
                except Exception:
                    pass
                self.done_callback(False)
                return

            uploaded = []
            if self.resume:
                uploaded = self.load_progress()
                self.log(f'Resuming. Already uploaded: {len(uploaded)}')

            remaining = [p for p in all_media if p.name not in uploaded]
            total = len(all_media)
            to_send = len(remaining)
            self.log(f'Total files: {total} Remaining: {to_send}')

            # batches
            if self.no_album:
                batches = [[p] for p in remaining]
            else:
                batches = [remaining[i:i+10] for i in range(0, len(remaining), 10)]

            batch_count = len(batches)
            files_sent = len(uploaded)
            start_time = time.time()
            # Shared counter for files sent (used by worker threads)
            self._files_sent = files_sent
            self._files_sent_lock = threading.Lock()

            def process_batch(batch_data):
                """Process a single batch of files with proper session management."""
                idx, batch = batch_data
                if self.stop_event.is_set():
                    return None

                # Initialize a fresh session for this batch
                if self.session is None:
                    self.init_session()

                files_processed = []
                try:
                    if len(batch) > 1 and not self.as_document:
                        # Attempt album upload
                        resp, processed_paths = self.send_media_group(self.chat_id, batch)
                        if processed_paths:
                            self.log(f'✅ Sent album {idx}/{batch_count} ({len(processed_paths)} items) first={processed_paths[0].name}')
                            for p in processed_paths:
                                files_processed.append(p.name)
                                self._post_upload_action(p)
                                try:
                                    with self._files_sent_lock:
                                        self._files_sent += 1
                                        current = self._files_sent
                                    elapsed = time.time() - start_time
                                    avg_per_file = elapsed / current if current else 0.001
                                    remaining_files = total - current
                                    eta = remaining_files * avg_per_file
                                    self.progress_callback(current, total, eta)
                                except Exception:
                                    pass
                        else:
                            self.log(f'⚠️ Album upload returned no processed paths (batch {idx}); falling back to singles')
                            for p in batch:
                                if self.stop_event.is_set():
                                    break
                                try:
                                    self.send_single_by_type(self.chat_id, p)
                                    self.log(f'✅ Sent {p.name}')
                                    files_processed.append(p.name)
                                    self._post_upload_action(p)
                                    try:
                                        with self._files_sent_lock:
                                            self._files_sent += 1
                                            current = self._files_sent
                                        elapsed = time.time() - start_time
                                        avg_per_file = elapsed / current if current else 0.001
                                        remaining_files = total - current
                                        eta = remaining_files * avg_per_file
                                        self.progress_callback(current, total, eta)
                                    except Exception:
                                        pass
                                except Exception as e2:
                                    self.log(f'❌ Failed to send {p.name}: {str(e2)}')
                                    if "429" in str(e2):
                                        self.init_session()
                                time.sleep(max(0, self.delay + random.uniform(0, self.jitter)))
                    else:
                        # Individual uploads
                        for p in batch:
                            if self.stop_event.is_set():
                                break
                            try:
                                self.send_single_by_type(self.chat_id, p)
                                self.log(f'✅ Sent {p.name}')
                                files_processed.append(p.name)
                                self._post_upload_action(p)
                                try:
                                    with self._files_sent_lock:
                                        self._files_sent += 1
                                        current = self._files_sent
                                    elapsed = time.time() - start_time
                                    avg_per_file = elapsed / current if current else 0.001
                                    remaining_files = total - current
                                    eta = remaining_files * avg_per_file
                                    self.progress_callback(current, total, eta)
                                except Exception:
                                    pass
                            except Exception as e:
                                self.log(f'❌ Failed to send {p.name}: {str(e)}')
                                if "429" in str(e):
                                    self.init_session()
                            time.sleep(max(0, self.delay + random.uniform(0, self.jitter)))
                except Exception as e:
                    self.log(f'❌ Fatal error in batch {idx}: {str(e)}')
                    return []
                return files_processed

            # Initialize fresh session before starting uploads
            self.init_session()
            # Initialize fresh session before starting uploads
            self.init_session()
            
            # Use thread pool for parallel uploads
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all batches to the thread pool
                future_to_batch = {executor.submit(process_batch, (i, b)): (i, b) 
                                 for i, b in enumerate(batches, 1)}
                
                uploaded_files = []
                for future in as_completed(future_to_batch):
                    if self.stop_event.is_set():
                        break
                    
                    batch_idx, _ = future_to_batch[future]
                    try:
                        result = future.result()
                        if result:
                            uploaded_files.extend(result)
                            files_sent = len(uploaded_files)
                            
                            # Update progress
                            elapsed = time.time() - start_time
                            avg_per_file = elapsed / files_sent if files_sent else 0.001
                            remaining_files = total - files_sent
                            eta = remaining_files * avg_per_file
                            self.progress_callback(files_sent, total, eta)
                            
                            # Save progress periodically
                            self.save_progress(uploaded_files)
                            
                            # Add small delay between batches
                            time.sleep(self.delay + random.uniform(0, self.jitter))
                    except Exception as e:
                        self.log(f'❌ Error processing batch {batch_idx}: {str(e)}')

            self.log('🎉 Done. Uploaded', files_sent, 'files.')
            # cleanup progress file
            try:
                if self.progress_file.exists():
                    self.progress_file.unlink()
            except Exception:
                pass
            self.done_callback(True)
        except Exception as e:
            self.log('Fatal error in upload worker:', e)
            self.done_callback(False)
        finally:
            # Ensure we close any open session
            try:
                if self.session:
                    self.session.close()
            except Exception:
                pass
            self.session = None


# ----------------- GUI -----------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Telegram Uploader — GUI')
        self.geometry('900x650')
        self.minsize(800, 600)
        # Allow root window to expand
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.config_file = Path.home() / '.telegram_uploader_config.json'
        self.logs_dir = Path.home() / '.telegram_uploader' / 'logs'
        # Persistent store for saved tokens and channels
        self.tokens_store_file = Path.home() / '.telegram_uploader_tokens.json'
        self.tokens_list: List[str] = []
        self.channels_list: List[str] = []
        
        # Initialize variables before creating widgets
        self.folder_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.chat_var = tk.StringVar()
        self.as_doc_var = tk.BooleanVar(value=False)
        self.no_album_var = tk.BooleanVar(value=False)
        # Option to skip token validation (useful when requests isn't available or offline)
        self.skip_validate_var = tk.BooleanVar(value=False)
        self.delay_var = tk.DoubleVar(value=1.0)
        self.jitter_var = tk.DoubleVar(value=0.4)
        self.max_workers_var = tk.IntVar(value=3)  # Default to 3 parallel uploads
        self.resume_var = tk.BooleanVar(value=True)
        self.delete_after_upload_var = tk.BooleanVar(value=True)
        self.include_link_var = tk.BooleanVar(value=True)
        self.channel_link_var = tk.StringVar(value='https://t.me/Telugu_InstaCuties')
        self.use_custom_caption_var = tk.BooleanVar(value=False)
        self.custom_caption_var = tk.StringVar(value='')
        self.caption_preview_var = tk.StringVar(value='')
        self.status_var = tk.StringVar(value='Idle')
        self.elapsed_var = tk.StringVar(value='Elapsed: 0s')
        self.filter_var = tk.StringVar(value='')
        self.caption_count_var = tk.StringVar(value='0/1024')
        self.log_lines: List[str] = []
        self.advanced_visible = True
        self.start_time_epoch = None
        self.custom_caption_text = None  # Text widget assigned later
        
        # Other instance variables
        self.worker = None
        self.stop_event = threading.Event()
        self.rate_limit_lock = threading.Lock()
        self.rate_limit_queue = Queue()
        
        # Create widgets and load settings
        self.setup_theme_and_fonts()
        self.create_widgets()
        self.load_settings()
        self.load_tokens_store()

    def setup_theme_and_fonts(self) -> None:
        """Apply a modern ttk theme (if available) and set default fonts to Lato with fallbacks."""
        try:
            # Try to ensure ttkthemes exists, then pick a theme
            theme_applied = False
            try:
                ensure_ttkthemes_available()
            except Exception:
                pass

            if 'ThemedStyle' in globals() and ThemedStyle is not None:
                try:
                    style = ThemedStyle(self)
                    # Prefer a modern clean theme
                    style.set_theme('arc')
                    theme_applied = True
                except Exception:
                    theme_applied = False
            if not theme_applied:
                style = ttk.Style(self)
                try:
                    for t in ('clam', 'alt', 'default'):
                        if t in style.theme_names():
                            style.theme_use(t)
                            break
                except Exception:
                    pass

            # Choose font family: Lato -> Helvetica -> Arial -> TkDefault
            try:
                families = {f.lower() for f in tkfont.families()}
            except Exception:
                families = set()
            if 'lato' in families:
                family = 'Lato'
            elif 'helvetica' in families:
                family = 'Helvetica'
            elif 'arial' in families:
                family = 'Arial'
            else:
                family = None
            size = 12
            if family:
                try:
                    # Set default for all Tk widgets (covers tk.Text, etc.)
                    self.option_add('*Font', f'{family} {size}')
                except Exception:
                    pass
                try:
                    # Set default for ttk widgets
                    style.configure('.', font=(family, size))
                    style.configure('TButton', padding=(8, 4))
                    style.configure('TLabel', padding=2)
                    style.configure('TEntry', padding=4)
                except Exception:
                    pass
            # Accent button style (dark/light adaptive)
            try:
                dark = detect_dark_mode()
                if dark:
                    accent_fg = '#ffffff'
                    accent_bg = '#7c3aed'  # purple
                    accent_bg_active = '#6d28d9'
                else:
                    accent_fg = '#ffffff'
                    accent_bg = '#14b8a6'  # teal
                    accent_bg_active = '#0d9488'
                style.configure('Accent.TButton', foreground=accent_fg, background=accent_bg)
                style.map('Accent.TButton', background=[('active', accent_bg_active), ('pressed', accent_bg_active)])
            except Exception:
                pass
            # Expose style on instance for later tweaks
            self.style = style
        except Exception:
            # Never let theme/font setup break the app
            try:
                self.style = ttk.Style(self)
            except Exception:
                self.style = None

    def save_settings(self):
        settings = {
            'token': self.token_var.get().strip(),
            'chat_id': self.chat_var.get().strip(),
            'as_document': self.as_doc_var.get(),
            'no_album': self.no_album_var.get(),
            'delay': self.delay_var.get(),
            'jitter': self.jitter_var.get(),
            'resume': self.resume_var.get(),
            'delete_after_upload': self.delete_after_upload_var.get(),
            'include_link': self.include_link_var.get(),
            'channel_link': self.channel_link_var.get().strip(),
            'use_custom_caption': self.use_custom_caption_var.get(),
            'custom_caption': (self.custom_caption_text.get('1.0', 'end-1c').strip() if self.custom_caption_text is not None else self.custom_caption_var.get().strip())
        }
        try:
            self.config_file.write_text(json.dumps(settings, indent=2))
        except Exception as e:
            messagebox.showwarning('Warning', f'Could not save settings: {e}')

    def load_settings(self):
        try:
            if self.config_file.exists():
                settings = json.loads(self.config_file.read_text())
                if settings.get('token'):
                    self.token_var.set(settings['token'])
                if settings.get('chat_id'):
                    self.chat_var.set(settings['chat_id'])
                if 'as_document' in settings:
                    self.as_doc_var.set(settings['as_document'])
                if 'no_album' in settings:
                    self.no_album_var.set(settings['no_album'])
                if 'delay' in settings:
                    self.delay_var.set(settings['delay'])
                if 'jitter' in settings:
                    self.jitter_var.set(settings['jitter'])
                if 'resume' in settings:
                    self.resume_var.set(settings['resume'])
                if 'delete_after_upload' in settings:
                    self.delete_after_upload_var.set(settings['delete_after_upload'])
                if 'include_link' in settings:
                    self.include_link_var.set(settings['include_link'])
                if 'channel_link' in settings:
                    self.channel_link_var.set(settings['channel_link'])
                if 'use_custom_caption' in settings:
                    self.use_custom_caption_var.set(settings['use_custom_caption'])
                if 'custom_caption' in settings:
                    # Populate both variable and Text widget
                    self.custom_caption_var.set(settings['custom_caption'])
                    if self.custom_caption_text is not None:
                        try:
                            self.custom_caption_text.delete('1.0', 'end')
                            self.custom_caption_text.insert('1.0', settings['custom_caption'])
                        except Exception:
                            pass
        except Exception as e:
            messagebox.showwarning('Warning', f'Could not load settings: {e}')

    def create_widgets(self):
        pad = 8
        # Top frame (folder, token, channel selectors)
        frm_top = ttk.Frame(self)
        frm_top.pack(fill='x', padx=pad, pady=pad)
        for i in range(3):
            frm_top.columnconfigure(i, weight=0)
        frm_top.columnconfigure(1, weight=1)

        # Folder selection
        ttk.Label(frm_top, text='Folder:').grid(row=0, column=0, sticky='w')
        self.entry_folder = ttk.Entry(frm_top, textvariable=self.folder_var)
        self.entry_folder.grid(row=0, column=1, sticky='ew')
        ttk.Button(frm_top, text='Browse', command=self.browse_folder).grid(row=0, column=2, padx=6)

        # Bot token
        ttk.Label(frm_top, text='Bot Token:').grid(row=1, column=0, sticky='w', pady=(6,0))
        self.entry_token = ttk.Entry(frm_top, textvariable=self.token_var, show='*')
        self.entry_token.grid(row=1, column=1, sticky='ew')
        ttk.Button(frm_top, text='Show', command=self.toggle_token).grid(row=1, column=2, padx=6)

        # Saved tokens selector and save button
        ttk.Label(frm_top, text='Saved Tokens:').grid(row=2, column=0, sticky='w', pady=(4,0))
        self.token_combo = ttk.Combobox(frm_top, state='readonly')
        self.token_combo.grid(row=2, column=1, sticky='ew', pady=(4,0))
        self.token_combo.bind('<<ComboboxSelected>>', lambda e: self.select_saved_token())
        ttk.Button(frm_top, text='Save Token', command=self.save_current_token).grid(row=2, column=2, padx=6, pady=(4,0))

        # Channel ID
        ttk.Label(frm_top, text='Channel ID:').grid(row=3, column=0, sticky='w', pady=(6,0))
        self.entry_chat = ttk.Entry(frm_top, textvariable=self.chat_var)
        self.entry_chat.grid(row=3, column=1, sticky='ew')
        ttk.Button(frm_top, text='Save Channel', command=self.save_current_channel).grid(row=3, column=2, padx=6, pady=(6,0))
        ttk.Label(frm_top, text='Saved Channels:').grid(row=4, column=0, sticky='w', pady=(4,0))
        self.channel_combo = ttk.Combobox(frm_top, state='readonly')
        self.channel_combo.grid(row=4, column=1, sticky='ew', pady=(4,0))
        self.channel_combo.bind('<<ComboboxSelected>>', lambda e: self.select_saved_channel())

        # Basic options frame (caption related)
        frm_basic = ttk.Frame(self)
        frm_basic.pack(fill='x', padx=pad)
        for i in range(3):
            frm_basic.columnconfigure(i, weight=1)
        ttk.Checkbutton(frm_basic, text='Send as document (preserve quality)', variable=self.as_doc_var, command=self.update_caption_preview).grid(row=0, column=0, sticky='w')
        ttk.Checkbutton(frm_basic, text='No album (send files individually)', variable=self.no_album_var).grid(row=0, column=1, sticky='w', padx=12)
        ttk.Checkbutton(frm_basic, text='Append channel link in caption', variable=self.include_link_var, command=self.update_caption_preview).grid(row=0, column=2, sticky='w', padx=(12,0))
        ttk.Label(frm_basic, text='Channel link:').grid(row=1, column=0, sticky='w', pady=(4,0))
        channel_link_entry = ttk.Entry(frm_basic, textvariable=self.channel_link_var)
        channel_link_entry.grid(row=1, column=1, sticky='ew', pady=(4,0))
        ttk.Checkbutton(frm_basic, text='Use custom caption base', variable=self.use_custom_caption_var, command=self.update_caption_preview).grid(row=1, column=2, sticky='w', pady=(4,0), padx=(12,0))
        ttk.Label(frm_basic, text='Custom caption (multi-line):').grid(row=2, column=0, sticky='nw', pady=(4,0))
        cap_container = ttk.Frame(frm_basic)
        cap_container.grid(row=2, column=1, sticky='nsew', pady=(4,0))
        frm_basic.rowconfigure(2, weight=1)
        cap_container.columnconfigure(0, weight=1)
        self.custom_caption_text = tk.Text(cap_container, height=4, wrap='word')
        self.custom_caption_text.grid(row=0, column=0, sticky='nsew')
        cap_scroll = ttk.Scrollbar(cap_container, orient='vertical', command=self.custom_caption_text.yview)
        cap_scroll.grid(row=0, column=1, sticky='ns')
        self.custom_caption_text.configure(yscrollcommand=cap_scroll.set)
        ttk.Button(frm_basic, text='Copy Caption', command=self.copy_current_caption).grid(row=2, column=2, sticky='w', pady=(4,0), padx=(12,0))
        ttk.Label(frm_basic, text='Caption preview:').grid(row=3, column=0, sticky='nw', pady=(6,0))
        preview_entry = ttk.Entry(frm_basic, textvariable=self.caption_preview_var, state='readonly')
        preview_entry.grid(row=3, column=1, columnspan=2, sticky='ew', pady=(6,0))
        try:
            self.channel_link_var.trace_add('write', lambda *a: self.update_caption_preview())
        except Exception:
            pass
        channel_link_entry.bind('<KeyRelease>', lambda e: self.update_caption_preview())
        if self.custom_caption_text is not None:
            self.custom_caption_text.bind('<KeyRelease>', lambda e: self.update_caption_preview())
        self.update_caption_preview()

        frm_prog = ttk.Frame(self)
        frm_prog.pack(fill='x', padx=pad, pady=(8,0))
        frm_prog.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(frm_prog, mode='determinate', length=100)
        self.progress.pack(fill='x', expand=True)
        ttk.Label(frm_prog, textvariable=self.status_var).pack(anchor='w', pady=(4,0))

        # Advanced toggle
        toggle_frame = ttk.Frame(self)
        toggle_frame.pack(fill='x', padx=pad, pady=(4,0))
        self.btn_adv_toggle = ttk.Button(toggle_frame, text='Hide Advanced ▴', command=self.toggle_advanced)
        self.btn_adv_toggle.pack(anchor='w')

        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(fill='x', padx=pad, pady=(6,0))
        for i in range(6):
            frm_buttons.columnconfigure(i, weight=1)
        self.btn_start = ttk.Button(frm_buttons, text='Start Upload', command=self.start_upload, style='Accent.TButton')
        self.btn_start.grid(row=0, column=0, sticky='ew', padx=4, pady=2)
        self.btn_stop = ttk.Button(frm_buttons, text='Stop', command=self.stop_upload, state='disabled')
        self.btn_stop.grid(row=0, column=1, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Test Connection', command=self.test_connection).grid(row=0, column=2, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Open Logs', command=self.open_logs_directory).grid(row=0, column=3, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Undo Last Move', command=self.undo_last_move).grid(row=0, column=4, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Open Progress File', command=self.open_progress_file).grid(row=0, column=5, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Clear Progress File', command=self.clear_progress_file).grid(row=1, column=0, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Save Settings', command=self.save_settings).grid(row=1, column=1, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Save As...', command=self.save_settings_as).grid(row=1, column=2, sticky='ew', padx=4, pady=2)
        ttk.Button(frm_buttons, text='Load Settings...', command=self.load_settings_from_file).grid(row=1, column=3, sticky='ew', padx=4, pady=2)
        ttk.Label(frm_buttons).grid(row=1, column=4, sticky='ew')
        ttk.Label(frm_buttons).grid(row=1, column=5, sticky='ew')

        # Log filter bar
        frm_filter = ttk.Frame(self)
        frm_filter.pack(fill='x', padx=pad, pady=(6,0))
        ttk.Label(frm_filter, text='Filter log:').pack(side='left')
        ent_filter = ttk.Entry(frm_filter, textvariable=self.filter_var)
        ent_filter.pack(side='left', fill='x', expand=True, padx=(6,0))
        ttk.Button(frm_filter, text='Clear', command=lambda: self.filter_var.set('')).pack(side='left', padx=6)
        # Tooltip example
        Tooltip(channel_link_entry, 'Channel link added to caption when enabled')
        frm_log = ttk.Frame(self)
        frm_log.pack(fill='both', expand=True, padx=pad, pady=(6, pad))
        frm_log.columnconfigure(0, weight=1)
        frm_log.rowconfigure(0, weight=1)
        log_scroll_y = ttk.Scrollbar(frm_log, orient='vertical')
        log_scroll_y.grid(row=0, column=1, sticky='ns')
        self.log_widget = tk.Text(frm_log, wrap='word', height=15, yscrollcommand=log_scroll_y.set)
        self.log_widget.grid(row=0, column=0, sticky='nsew')
        log_scroll_y.config(command=self.log_widget.yview)
        self.log_widget.configure(state='disabled')
        # Status bar (elapsed time)
        status_bar = ttk.Frame(self)
        status_bar.pack(fill='x', padx=pad, pady=(0,6))
        ttk.Label(status_bar, textvariable=self.elapsed_var).pack(side='right')
        # Trace filter changes
        try:
            self.filter_var.trace_add('write', lambda *a: self.refresh_log_view())
        except Exception:
            pass
        # Schedule elapsed timer
        self.after(1000, self._tick_elapsed)
        # Add caption length indicator near preview
        ttk.Label(frm_basic, textvariable=self.caption_count_var).grid(row=3, column=2, sticky='e', padx=(0,4))

        # Advanced frame content (moved timing & execution controls here)
        self.advanced_frame = ttk.Frame(self)
        self.advanced_frame.pack(fill='x', padx=pad, pady=(4,0))
        for i in range(3):
            self.advanced_frame.columnconfigure(i, weight=1)
        ttk.Label(self.advanced_frame, text='Delay (s):').grid(row=0, column=0, sticky='w')
        ttk.Entry(self.advanced_frame, textvariable=self.delay_var, width=8).grid(row=0, column=0, sticky='e')
        ttk.Label(self.advanced_frame, text='Jitter (s):').grid(row=0, column=1, sticky='w')
        ttk.Entry(self.advanced_frame, textvariable=self.jitter_var, width=8).grid(row=0, column=1, sticky='e')
        ttk.Label(self.advanced_frame, text='Parallel uploads:').grid(row=0, column=2, sticky='w', padx=(12,0))
        ttk.Entry(self.advanced_frame, textvariable=self.max_workers_var, width=3).grid(row=0, column=2, sticky='e')
        ttk.Checkbutton(self.advanced_frame, text='Resume previous run if progress found', variable=self.resume_var).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4,0))
        ttk.Checkbutton(self.advanced_frame, text='Move files to .uploaded after upload (safer)', variable=self.delete_after_upload_var).grid(row=1, column=2, sticky='w', pady=(4,0))
        ttk.Checkbutton(self.advanced_frame, text='Skip bot token validation (use with caution)', variable=self.skip_validate_var).grid(row=2, column=0, columnspan=3, sticky='w', pady=(4,0))
        # Tooltips for advanced controls
        Tooltip(self.advanced_frame.children.get('!entry'), 'Base delay added between sends.')
        # Find jitter entry by iterating children (simple heuristic)
        for w in self.advanced_frame.winfo_children():
            try:
                if isinstance(w, ttk.Entry) and w is not self.advanced_frame.children.get('!entry'):
                    Tooltip(w, 'Random jitter (0–value seconds) added to delay.')
                    break
            except Exception:
                pass
        Tooltip(self.advanced_frame.children.get('!checkbutton'), 'Attempt to resume from existing .upload_progress.json')
        # parallel uploads entry tooltip
        try:
            for w in self.advanced_frame.winfo_children():
                if isinstance(w, ttk.Entry) and w['width'] == '3':
                    Tooltip(w, 'Maximum parallel upload workers (1-10).')
        except Exception:
            pass
    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_var.set(path)

    # ---------------- Saved tokens/channels persistence -----------------
    def load_tokens_store(self):
        """Load saved tokens/channels from JSON store into combos."""
        try:
            if self.tokens_store_file.exists():
                data = json.loads(self.tokens_store_file.read_text())
                tokens = data.get('tokens', [])
                channels = data.get('channels', [])
                # Deduplicate while preserving order
                def dedupe(seq):
                    seen = set()
                    out = []
                    for item in seq:
                        item = item.strip()
                        if not item:
                            continue
                        if item not in seen:
                            seen.add(item)
                            out.append(item)
                    return out
                self.tokens_list = dedupe(tokens)[:50]
                self.channels_list = dedupe(channels)[:50]
            else:
                self.tokens_list = []
                self.channels_list = []
        except Exception:
            self.tokens_list = []
            self.channels_list = []
        self.update_tokens_channels_combos()

    def save_tokens_store(self):
        try:
            payload = {
                'tokens': self.tokens_list,
                'channels': self.channels_list
            }
            self.tokens_store_file.write_text(json.dumps(payload, indent=2))
        except Exception as e:
            self.append_log(f'Warning: could not save tokens store: {e}')

    def update_tokens_channels_combos(self):
        try:
            if hasattr(self, 'token_combo'):
                self.token_combo['values'] = self.tokens_list
            if hasattr(self, 'channel_combo'):
                self.channel_combo['values'] = self.channels_list
        except Exception:
            pass

    def save_current_token(self):
        tok = self.token_var.get().strip()
        if not tok:
            return
        if tok not in self.tokens_list:
            self.tokens_list.insert(0, tok)
            self.tokens_list = self.tokens_list[:50]
            self.save_tokens_store()
            self.update_tokens_channels_combos()

    def save_current_channel(self):
        chan = self.chat_var.get().strip()
        if not chan:
            return
        if chan not in self.channels_list:
            self.channels_list.insert(0, chan)
            self.channels_list = self.channels_list[:50]
            self.save_tokens_store()
            self.update_tokens_channels_combos()

    def update_caption_preview(self):
        """Recompute caption preview using current GUI settings."""
        if self.use_custom_caption_var.get():
            if self.custom_caption_text is not None:
                base = self.custom_caption_text.get('1.0', 'end-1c').strip()
            else:
                base = self.custom_caption_var.get().strip()
        else:
            base = Path(self.folder_var.get().strip()).name if self.folder_var.get().strip() else ''
        if not base:
            base = '(folder name)'
        if self.include_link_var.get() and self.channel_link_var.get().strip():
            caption = f"Instagram ID : {base}\n{self.channel_link_var.get().strip()}"
        else:
            caption = f"Instagram ID : {base}"
        trimmed = caption[:1024]
        self.caption_preview_var.set(trimmed)
        try:
            self.caption_count_var.set(f"{len(trimmed)}/1024")
        except Exception:
            pass

    def copy_current_caption(self):
        try:
            self.clipboard_clear()
            self.clipboard_append(self.caption_preview_var.get())
            self.append_log('📋 Caption copied to clipboard')
        except Exception as e:
            self.append_log(f'Failed to copy caption: {e}')

    def select_saved_token(self):
        sel = self.token_combo.get().strip()
        if sel:
            self.token_var.set(sel)

    def select_saved_channel(self):
        sel = self.channel_combo.get().strip()
        if sel:
            self.chat_var.set(sel)



    def toggle_token(self):
        if self.entry_token.cget('show') == '*':
            self.entry_token.config(show='')
        else:
            self.entry_token.config(show='*')

    def append_log(self, text: str) -> None:
        """Thread-safe append to GUI log and persistent log file.

        This schedules the actual GUI update on the main thread via `after()` when
        called from a worker thread.
        """
        # Helper that performs the actual append on the main thread
        def _impl(msg: str):
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            log_message = f'[{timestamp}] {msg}\n'

            # Keep full history for filtering
            try:
                self.log_lines.append(log_message)
            except Exception:
                pass
            # Display filtered view in GUI
            try:
                self.log_widget.configure(state='normal')
                self.log_widget.delete('1.0', 'end')
                flt = (self.filter_var.get() or '').strip().lower()
                for ln in self.log_lines:
                    if not flt or flt in ln.lower():
                        self.log_widget.insert('end', ln)
                self.log_widget.see('end')
                self.log_widget.configure(state='disabled')
            except tk.TclError:
                pass

            # Echo to stdout so external wrappers (SwiftUI launcher) can parse lines
            try:
                print(log_message.rstrip())
            except Exception:
                pass

            # Save to log file (best-effort)
            try:
                logs_dir = Path.home() / '.telegram_uploader' / 'logs'
                logs_dir.mkdir(parents=True, exist_ok=True)
                log_file = logs_dir / f'upload_{time.strftime("%Y%m%d")}.log'
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_message)
            except Exception:
                # don't crash on logging failure
                pass

        # If we're on the main thread, run immediately, otherwise schedule
        if threading.current_thread() is threading.main_thread():
            _impl(text)
        else:
            try:
                # schedule on main loop
                self.after(0, lambda: _impl(text))
            except Exception:
                # if scheduling fails, try to write the log file directly
                try:
                    logs_dir = Path.home() / '.telegram_uploader' / 'logs'
                    logs_dir.mkdir(parents=True, exist_ok=True)
                    log_file = logs_dir / f'upload_{time.strftime("%Y%m%d")}.log'
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {text}\n')
                except Exception:
                    pass

    def progress_cb(self, sent: int, total: int, eta_seconds: float) -> None:
        """Thread-safe progress callback. Schedules UI updates on the main thread.

        The worker threads call this method; we then ensure the actual widget
        modifications happen on the Tk mainloop via `after()`.
        """
        def _apply(s: int, t: int, eta: float):
            try:
                # Ensure a sensible maximum
                if t <= 0:
                    t = 1
                self.progress['maximum'] = t
                self.progress['value'] = min(s, t)

                percentage = (s / t * 100) if t > 0 else 0
                eta_text = f"ETA: {self.seconds_to_hms(eta)}" if eta and eta > 1 else 'ETA: <1s'
                self.status_var.set(f'Sent {s}/{t} ({percentage:.1f}%) — {eta_text}')
                # Keep the UI responsive
                try:
                    self.update_idletasks()
                except tk.TclError:
                    pass

                # Emit structured JSON progress event to stdout for external parsers
                try:
                    event = {
                        'type': 'progress',
                        'sent': int(s),
                        'total': int(t),
                        'eta_seconds': float(eta) if eta is not None else None,
                        'timestamp': time.time()
                    }
                    print(json.dumps(event), flush=True)
                except Exception:
                    pass
            except Exception:
                pass

        # If on main thread, apply directly, otherwise schedule
        if threading.current_thread() is threading.main_thread():
            _apply(sent, total, eta_seconds)
        else:
            try:
                self.after(0, lambda: _apply(sent, total, eta_seconds))
            except Exception:
                # ignore scheduling failures
                pass

    def seconds_to_hms(self, s: float) -> str:
        """Convert seconds to a human-readable string format (e.g., 1h 30m 45s)"""
        try:
            total_seconds = int(max(0, s))
        except (ValueError, TypeError):
            return '0s'
            
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def open_progress_file(self):
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showinfo('No folder', 'Choose a folder first')
            return
        path = Path(folder) / '.upload_progress.json'
        if not path.exists():
            messagebox.showinfo('Not found', 'No progress file in folder')
            return
        os.system(f'open "{str(path)}"')

    def clear_progress_file(self):
        folder = self.folder_var.get().strip()
        if not folder:
            return
        path = Path(folder) / '.upload_progress.json'
        if path.exists():
            try:
                path.unlink()
                messagebox.showinfo('Cleared', 'Progress file removed')
            except Exception as e:
                messagebox.showerror('Error', f'Could not remove: {e}')
        else:
            messagebox.showinfo('No file', 'No progress file to remove')

    def open_logs_directory(self):
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            os.system(f'open "{str(self.logs_dir)}"')
        except Exception as e:
            messagebox.showerror('Error', f'Could not open logs directory: {e}')

    def undo_last_move(self):
        """Restore the most recently moved file from the .uploaded folder back to the main folder."""
        folder = self.folder_var.get().strip()
        if not folder:
            messagebox.showinfo('No folder', 'Choose a folder first')
            return
        uploaded_dir = Path(folder) / '.uploaded'
        if not uploaded_dir.exists():
            messagebox.showinfo('Nothing to undo', 'No .uploaded folder found')
            return
        files = sorted([p for p in uploaded_dir.iterdir() if p.is_file()], key=lambda x: x.stat().st_mtime, reverse=True)
        if not files:
            messagebox.showinfo('Nothing to undo', 'No files to restore in .uploaded')
            return
        src = files[0]
        dest = Path(folder) / src.name
        try:
            # avoid clobber
            if dest.exists():
                base = dest.stem
                suff = dest.suffix
                i = 1
                while True:
                    candidate = Path(folder) / f"{base}_restored_{i}{suff}"
                    if not candidate.exists():
                        dest = candidate
                        break
                    i += 1
            src.replace(dest)
            self.append_log(f'↩️ Restored {src.name} to folder')
        except Exception as e:
            messagebox.showerror('Error', f'Could not restore: {e}')

    def validate_token(self, token: str) -> bool:
        if not token:
            return False

        # Accept tokens in a few common pasted formats: raw token, prefixed with "Bot ",
        # or a full api URL (extract the token using regex).
        try:
            # Try to extract a token pattern like <digits>:<chars>
            m = re.search(r"(\d+:[-A-Za-z0-9_]+)", token)
            token_clean = m.group(1) if m else token.strip()

            url = f"https://api.telegram.org/bot{token_clean}/getMe"

            # Prefer requests if available, otherwise use urllib as a fallback so the
            # validation can work even in locked/system-managed Python installs.
            j = None
            if requests is not None:
                resp = requests.get(url, timeout=10)
                try:
                    j = resp.json()
                except Exception:
                    j = None
            else:
                try:
                    with urllib.request.urlopen(url, timeout=10) as r:
                        data = r.read()
                        j = json.loads(data.decode('utf-8'))
                except urllib.error.URLError as ue:
                    # network error
                    try:
                        self.append_log(f'validate_token urllib error: {ue}')
                    except Exception:
                        pass
                    return False
                except Exception:
                    j = None

            ok = False
            if isinstance(j, dict):
                ok = bool(j.get('ok'))
                if not ok:
                    try:
                        self.append_log(f'validate_token: API returned not-ok: {j}')
                    except Exception:
                        pass
            else:
                try:
                    self.append_log(f'validate_token: non-json or empty response')
                except Exception:
                    pass

            return ok
        except Exception as e:
            # Network or other error — log for user visibility
            try:
                self.append_log(f'validate_token exception: {e}')
            except Exception:
                pass
            return False

    def test_connection(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showerror('Error', 'Please enter a bot token')
            return
        
        self.btn_start.config(state='disabled')
        try:
            if self.skip_validate_var.get():
                # User chose to skip network validation
                try:
                    self.append_log('Skipping token validation (user opted out).')
                except Exception:
                    pass
                messagebox.showinfo('Skipped', 'Token validation skipped (per setting)')
            else:
                if self.validate_token(token):
                    messagebox.showinfo('Success', 'Bot token is valid!')
                else:
                    messagebox.showerror('Error', 'Invalid bot token')
        finally:
            self.btn_start.config(state='normal')

    def start_upload(self):
        folder = self.folder_var.get().strip()
        token = self.token_var.get().strip()
        chat_id = self.chat_var.get().strip()

        if not folder or not token or not chat_id:
            messagebox.showerror('Missing', 'Folder, Bot Token, and Channel ID are required')
            return
            
        if not self.skip_validate_var.get():
            if not self.validate_token(token):
                messagebox.showerror('Error', 'Invalid bot token')
                return
        else:
            try:
                self.append_log('Skipping token validation (user opted out).')
            except Exception:
                pass

        fpath = Path(folder)
        if not fpath.exists() or not fpath.is_dir():
            messagebox.showerror('Invalid folder', 'Folder invalid')
            return

        # Ensure requests is available (auto-install if needed)
        if requests is None:
            if not ensure_requests_available():
                messagebox.showerror('Missing dependency', 'Could not import or auto-install the requests library. Please install manually: python -m pip install requests')
                return

        # Reset UI state
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.stop_event.clear()
        
        # Clear log & in-memory list
        self.log_widget.configure(state='normal')
        self.log_widget.delete('1.0', 'end')
        self.log_widget.configure(state='disabled')
        self.log_lines.clear()
        
        # Reset progress bar and status
        self.progress['value'] = 0
        self.progress['maximum'] = 100
        self.status_var.set('Starting upload...')

        self.worker = UploadWorker(
            folder=fpath,
            token=token,
            chat_id=chat_id,
            as_document=self.as_doc_var.get(),
            no_album=self.no_album_var.get(),
            delay=self.delay_var.get(),
            jitter=self.jitter_var.get(),
            resume=self.resume_var.get(),
            delete_after_upload=self.delete_after_upload_var.get(),
            max_workers=self.max_workers_var.get(),
            progress_callback=self.progress_cb,
            log_callback=self.append_log,
            done_callback=self.upload_done,
            stop_event=self.stop_event,
            include_link=self.include_link_var.get(),
            channel_link=self.channel_link_var.get().strip(),
            use_custom_caption=self.use_custom_caption_var.get(),
            custom_caption=(self.custom_caption_text.get('1.0', 'end-1c').strip() if self.custom_caption_text is not None else self.custom_caption_var.get().strip())
        )
        self.append_log('Starting upload...')
        self.start_time_epoch = time.time()
        self.worker.start()

    def stop_upload(self):
        if messagebox.askyesno('Stop', 'Stop the upload? This will save progress and stop.'):
            self.stop_event.set()
            self.btn_stop.config(state='disabled')
            self.append_log('Stop requested — waiting for worker to finish...')

    def upload_done(self, success: bool):
        self.append_log('Upload finished' if success else 'Upload stopped / failed')
        self.btn_start.config(state='normal')
        self.btn_stop.config(state='disabled')
        self.start_time_epoch = None
        # Emit structured completion event
        try:
            event = {
                'type': 'done',
                'success': bool(success),
                'timestamp': time.time()
            }
            print(json.dumps(event), flush=True)
        except Exception:
            pass

    def save_settings_as(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension='.json',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            initialdir=str(Path.home()),
            title='Save Settings As'
        )
        if file_path:
            self.config_file = Path(file_path)
            self.save_settings()
            messagebox.showinfo('Success', 'Settings saved successfully!')

    def load_settings_from_file(self):
        file_path = filedialog.askopenfilename(
            defaultextension='.json',
            filetypes=[('JSON files', '*.json'), ('All files', '*.*')],
            initialdir=str(Path.home()),
            title='Load Settings'
        )
        if file_path:
            old_config = self.config_file
            self.config_file = Path(file_path)
            try:
                self.load_settings()
                messagebox.showinfo('Success', 'Settings loaded successfully!')
            except Exception as e:
                self.config_file = old_config
                messagebox.showerror('Error', f'Failed to load settings: {e}')

    def toggle_advanced(self):
        try:
            self.advanced_visible = not self.advanced_visible
            if self.advanced_visible:
                self.btn_adv_toggle.config(text='Hide Advanced ▴')
                # Show advanced frame if we add content later
                if self.advanced_frame.winfo_manager() == '':
                    self.advanced_frame.pack(fill='x', padx=8, pady=4)
            else:
                self.btn_adv_toggle.config(text='Show Advanced ▾')
                if self.advanced_frame.winfo_manager() != '':
                    self.advanced_frame.forget()
        except Exception:
            pass

    def refresh_log_view(self):
        try:
            self.log_widget.configure(state='normal')
            self.log_widget.delete('1.0', 'end')
            flt = (self.filter_var.get() or '').strip().lower()
            for ln in self.log_lines:
                if not flt or flt in ln.lower():
                    self.log_widget.insert('end', ln)
            self.log_widget.see('end')
            self.log_widget.configure(state='disabled')
        except Exception:
            pass

    def _tick_elapsed(self):
        try:
            if self.start_time_epoch:
                elapsed = time.time() - float(self.start_time_epoch)
                self.elapsed_var.set(f'Elapsed: {self.seconds_to_hms(elapsed)}')
            else:
                self.elapsed_var.set('Elapsed: 0s')
        except Exception:
            pass
        finally:
            try:
                self.after(1000, self._tick_elapsed)
            except Exception:
                pass


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Telegram Uploader GUI/CLI')
    parser.add_argument('--folder', type=str, help='Folder with media to upload')
    parser.add_argument('--token', type=str, help='Bot token')
    parser.add_argument('--channel', type=str, help='Channel ID (@channelusername or numeric)')
    parser.add_argument('--as-document', action='store_true', help='Send files as documents')
    parser.add_argument('--no-album', action='store_true', help='Send files individually')
    parser.add_argument('--delay', type=float, default=1.0, help='Base delay between sends')
    parser.add_argument('--jitter', type=float, default=0.4, help='Random jitter added to delay')
    parser.add_argument('--resume', action='store_true', help='Resume if progress file exists')
    parser.add_argument('--move-after', action='store_true', help='Move files to .uploaded after upload')
    parser.add_argument('--workers', type=int, default=3, help='Parallel upload workers (1-10)')
    parser.add_argument('--include-link', action='store_true', help='Append channel link in caption')
    parser.add_argument('--link', type=str, default='', help='Channel link to append')
    parser.add_argument('--use-custom-caption', action='store_true', help='Use custom caption as base')
    parser.add_argument('--custom-caption', type=str, default='', help='Custom caption text')
    parser.add_argument('--skip-validate', action='store_true', help='Skip bot token validation')

    args, unknown = parser.parse_known_args()

    # If CLI core args are provided, run headless; otherwise, launch GUI
    if args.folder and args.token and args.channel:
        # Ensure requests
        if requests is None:
            if not ensure_requests_available():
                print('[ERROR] requests library missing and auto-install failed.', flush=True)
                sys.exit(2)

        # Optional validation
        if not args.skip_validate:
            try:
                # Lightweight validation using urllib if requests not available already handled
                url = f"https://api.telegram.org/bot{args.token.strip()}/getMe"
                ok = False
                try:
                    if requests is not None:
                        r = requests.get(url, timeout=10)
                        j = r.json()
                        ok = bool(j.get('ok')) if isinstance(j, dict) else False
                    else:
                        with urllib.request.urlopen(url, timeout=10) as r:
                            j = json.loads(r.read().decode('utf-8'))
                            ok = bool(j.get('ok')) if isinstance(j, dict) else False
                except Exception as ve:
                    print(f"[WARN] validate_token failed: {ve}", flush=True)
                if not ok:
                    print('[ERROR] Invalid bot token (use --skip-validate to bypass).', flush=True)
                    sys.exit(3)
            except Exception:
                pass

        folder = Path(args.folder).expanduser()
        if not folder.exists() or not folder.is_dir():
            print('[ERROR] Folder not found or not a directory:', folder, flush=True)
            sys.exit(4)

        stop_event = threading.Event()

        def headless_progress(sent, total, eta):
            try:
                event = {
                    'type': 'progress',
                    'sent': int(sent),
                    'total': int(total) if total else 0,
                    'eta_seconds': float(eta) if eta is not None else None,
                    'timestamp': time.time()
                }
                print(json.dumps(event), flush=True)
            except Exception:
                pass

        def headless_log(msg: str):
            try:
                timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f'[{timestamp}] {msg}', flush=True)
            except Exception:
                pass

        def headless_done(success: bool):
            try:
                ev = {'type': 'done', 'success': bool(success), 'timestamp': time.time()}
                print(json.dumps(ev), flush=True)
            except Exception:
                pass

        worker = UploadWorker(
            folder=folder,
            token=args.token.strip(),
            chat_id=args.channel.strip(),
            as_document=bool(args.as_document),
            no_album=bool(args.no_album),
            delay=float(args.delay),
            jitter=float(args.jitter),
            resume=bool(args.resume),
            delete_after_upload=bool(args.move_after),
            max_workers=max(1, min(10, int(args.workers))),
            progress_callback=headless_progress,
            log_callback=headless_log,
            done_callback=headless_done,
            stop_event=stop_event,
            include_link=bool(args.include_link) or bool(args.link),
            channel_link=(args.link or '').strip(),
            use_custom_caption=bool(args.use_custom_caption),
            custom_caption=(args.custom_caption or '').strip()
        )
        headless_log('Starting upload (headless)...')
        worker.start()
        # Wait for completion using join to ensure clean exit
        try:
            while worker.is_alive():
                worker.join(timeout=0.25)
        except KeyboardInterrupt:
            stop_event.set()
            headless_log('Stop requested (KeyboardInterrupt). Waiting for worker to finish...')
            while worker.is_alive():
                worker.join(timeout=0.25)
        sys.exit(0)
    else:
        app = App()
        app.mainloop()
