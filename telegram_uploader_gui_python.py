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
from tkinter import ttk, filedialog, messagebox, simpledialog
import tkinter.font as tkfont
from typing import List, Dict
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

# Optional: ttkbootstrap for modern, rounded widgets
ttkb = None  # type: ignore
try:
    # Allow launcher to disable ttkbootstrap to avoid runtime incompatibilities
    if os.environ.get('DISABLE_TTKBOOTSTRAP') != '1':
        import ttkbootstrap as ttkb  # type: ignore
except Exception:
    ttkb = None  # type: ignore

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

from typing import Optional, Any

class Tooltip:
    def __init__(self, widget: tk.Widget, text: str, delay: int = 400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._id = None
        self._tip = None
        widget.bind('<Enter>', self._schedule)
        widget.bind('<Leave>', self._hide)

    def _schedule(self, _event: Optional[Any] = None):
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

    def _hide(self, _event: Optional[Any] = None):
        self._unschedule()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

# ----------------- Material 3 (inspired) palette & theming -----------------
def material3_palette(dark: bool = False) -> Dict[str, str]:
    """Return a baseline Material 3-inspired color palette.
    These are approximations of M3 baseline tones; not a full dynamic color system.
    """
    if not dark:
        return {
            'primary': '#6750A4',
            'onPrimary': '#FFFFFF',
            'primaryContainer': '#EADDFF',
            'onPrimaryContainer': '#21005D',
            'secondary': '#625B71',
            'onSecondary': '#FFFFFF',
            'secondaryContainer': '#E8DEF8',
            'onSecondaryContainer': '#1D192B',
            'surface': '#FFFBFE',
            'onSurface': '#1C1B1F',
            'surfaceVariant': '#E7E0EC',
            'onSurfaceVariant': '#49454F',
            'outline': '#79747E',
            'error': '#B3261E',
            'onError': '#FFFFFF',
            'inverseSurface': '#313033',
            'inverseOnSurface': '#F4EFF4',
            'inversePrimary': '#D0BCFF',
        }
    else:
        return {
            'primary': '#D0BCFF',
            'onPrimary': '#381E72',
            'primaryContainer': '#4F378B',
            'onPrimaryContainer': '#EADDFF',
            'secondary': '#CCC2DC',
            'onSecondary': '#332D41',
            'secondaryContainer': '#4A4458',
            'onSecondaryContainer': '#E8DEF8',
            'surface': '#1C1B1F',
            'onSurface': '#E6E1E6',
            'surfaceVariant': '#49454F',
            'onSurfaceVariant': '#CAC4D0',
            'outline': '#938F99',
            'error': '#F2B8B5',
            'onError': '#601410',
            'inverseSurface': '#E6E1E6',
            'inverseOnSurface': '#313033',
            'inversePrimary': '#6750A4',
        }

def apply_material3_styles(root: tk.Tk, style: ttk.Style) -> Dict[str, str]:
    """Apply Material 3-inspired styles to ttk and tk widgets. Returns the palette used."""
    try:
        dark = detect_dark_mode()
    except Exception:
        dark = False
    palette = material3_palette(dark)

    # Use a themable engine that allows color overrides (avoid aqua)
    try:
        if 'clam' in style.theme_names():
            style.theme_use('clam')
    except Exception:
        pass

    # Root background and defaults
    try:
        root.configure(bg=palette['surface'])
    except Exception:
        pass

    # Base widget backgrounds/foregrounds
    try:
        style.configure('.', background=palette['surface'], foreground=palette['onSurface'])
        style.configure('TFrame', background=palette['surface'])
        style.configure('TLabelframe', background=palette['surface'])
        style.configure('TLabelframe.Label', background=palette['surface'], foreground=palette['onSurface'])
        style.configure('TLabel', background=palette['surface'], foreground=palette['onSurface'])
        style.configure('TCheckbutton', background=palette['surface'], foreground=palette['onSurface'])
        style.configure('TRadiobutton', background=palette['surface'], foreground=palette['onSurface'])
        # Entries
        style.configure('TEntry', fieldbackground=palette['surface'], foreground=palette['onSurface'])
        # Progressbar
        style.configure('TProgressbar', background=palette['primary'])
    except Exception:
        pass

    # Buttons: primary, secondary, danger, outline mapped to M3 tones
    try:
        # Primary
        style.configure('M3Primary.TButton', background=palette['primary'], foreground=palette['onPrimary'], relief='flat', padding=(10, 6))
        style.map('M3Primary.TButton', background=[('active', palette['inversePrimary']), ('pressed', palette['inversePrimary'])])
        # Secondary: use surfaceVariant
        style.configure('M3Secondary.TButton', background=palette['surfaceVariant'], foreground=palette['onSurface'], relief='flat', padding=(10, 6))
        style.map('M3Secondary.TButton', background=[('active', palette['secondaryContainer']), ('pressed', palette['secondaryContainer'])])
        # Outline
        style.configure('M3Outline.TButton', background=palette['surface'], foreground=palette['onSurface'], relief='groove', padding=(10, 6))
        # Danger
        style.configure('M3Danger.TButton', background=palette['error'], foreground=palette['onError'], relief='flat', padding=(10, 6))
        style.map('M3Danger.TButton', background=[('active', '#D32F2F'), ('pressed', '#D32F2F')])
    except Exception:
        pass

    return palette

# Supported extensions (used for logging and checks)
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}
VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}

# ----------------- Helper functions (networking & upload) -----------------

def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXTS


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXTS

# ----------------- Dependency helper -----------------
def ensure_requests_available() -> bool:
    """Ensure the requests library is importable. Attempts auto-install if missing.
    Returns True if available, False otherwise."""
    global requests
    if requests is not None:
        return True
    # If runtime pip installs are not explicitly allowed, do not attempt network installs
    if os.environ.get('ALLOW_RUNTIME_PIP') != '1':
        return False
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
    # Respect environment flags to disable optional theme dependencies in packaged apps
    if os.environ.get('DISABLE_TTKTHEMES') == '1' or os.environ.get('DISABLE_TTKBOOTSTRAP') == '1':
        # Do not attempt any runtime installation or import
        return False
    if ThemedStyle is not None:
        return True
    # If runtime pip installs are not explicitly allowed, skip auto-install
    allow_runtime_pip = os.environ.get('ALLOW_RUNTIME_PIP') == '1'
    if not allow_runtime_pip:
        return False
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
        
    def request_with_retries(self, url: str, data=None, files=None, context='', max_retries=None,
                              connect_timeout: float = 15.0, read_timeout: float = 90.0):
        """Make HTTP request with retries and session management.
        Adds split connect/read timeout and optional per-call retry override to avoid long initial hangs.
        """
        if not self.session:
            self.init_session()

        retries = int(max_retries if max_retries is not None else self.MAX_RETRIES)
        attempt = 0
        while attempt < retries and not self.stop_event.is_set():
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
                resp = self.session.post(
                    url, data=data, files=files_for_requests,
                    timeout=(float(connect_timeout), float(read_timeout))
                )
            except requests.Timeout as e:
                backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                self.log(f'Timeout ({context}) attempt {attempt}/{retries}:', e, f'backoff={backoff:.1f}s')
                for of in opened_here:
                    try:
                        of.close()
                    except Exception:
                        pass
                time.sleep(backoff)
                continue
            except requests.RequestException as e:
                backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                self.log(f'Network error ({context}) attempt {attempt}/{retries}:', e, f'backoff={backoff:.1f}s')
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

        raise RuntimeError(f'Failed after {retries} attempts for {context}')

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
            j = self.request_with_retries(
                url, data=data, files=files,
                context=f'media_group {valid_paths[0].name}',
                max_retries=2, connect_timeout=15.0, read_timeout=75.0
            )
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
                GROUP_SIZE = 5
                batches = [remaining[i:i+GROUP_SIZE] for i in range(0, len(remaining), GROUP_SIZE)]

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

class CollapsibleSection(ttk.Frame):
    """A simple collapsible section with a header button and a content frame.

    Usage:
        sec = CollapsibleSection(parent, title='My Section', initially_collapsed=False,
                                 fill='x', expand=False)
        sec.pack(fill='x', padx=8, pady=4)
        container = sec.content  # place inner widgets into this frame
    """
    def __init__(self, parent, title: str, initially_collapsed: bool = False,
                 fill: str = 'x', expand: bool = False):
        super().__init__(parent)
        self._title = title
        self._fill = fill
        self._expand = expand
        self._collapsed = bool(initially_collapsed)

        # Header button (flat toolbutton style looks nicer for section headers)
        self.header = ttk.Button(self, text='', command=self.toggle, style='Toolbutton')
        self.header.pack(fill='x', anchor='w')

        # Content container
        self.content = ttk.Frame(self)

        # Initialize state
        self._refresh_header()
        if not self._collapsed:
            self._show()

    def _refresh_header(self):
        try:
            arrow = '▸' if self._collapsed else '▾'
            self.header.config(text=f'{arrow} {self._title}')
        except Exception:
            pass

    def _show(self):
        try:
            self.content.pack(fill=self._fill, expand=self._expand)
        except Exception:
            pass

    def _hide(self):
        try:
            if self.content.winfo_manager() != '':
                self.content.forget()
        except Exception:
            pass

    def toggle(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._hide()
        else:
            self._show()
        self._refresh_header()

    def expand(self):
        try:
            if self._collapsed:
                self.toggle()
        except Exception:
            pass

    def collapse(self):
        try:
            if not self._collapsed:
                self.toggle()
        except Exception:
            pass

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Telegram Uploader — GUI')
        # Detect screen size and enable a compact mode for small displays
        try:
            screen_w = self.winfo_screenwidth()
            screen_h = self.winfo_screenheight()
        except Exception:
            screen_w, screen_h = (1024, 768)
        # Compact if height is small (e.g., 800 on many 11" MacBook Airs)
        self.compact_mode = bool(screen_h <= 820)
        if self.compact_mode:
            self.geometry('820x560')
            self.minsize(700, 520)
            self.base_pad = 6
        else:
            self.geometry('900x650')
            self.minsize(800, 600)
            self.base_pad = 8
        # Allow root window to expand
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.config_file = Path.home() / '.telegram_uploader_config.json'
        self.logs_dir = Path.home() / '.telegram_uploader' / 'logs'
        # Persistent store for saved tokens and channels
        self.tokens_store_file = Path.home() / '.telegram_uploader_tokens.json'
        self.tokens_list = []
        self.channels_list = []
        
        # Initialize variables before creating widgets
        self.folder_var = tk.StringVar()
        self.token_var = tk.StringVar()
        self.chat_var = tk.StringVar()
        self.channel_name_var = tk.StringVar()
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
        self.log_lines = []
        self.advanced_visible = True
        self.start_time_epoch = None
        self.custom_caption_text = None  # Text widget assigned later
        # Multi-folder selection state
        self.folders_list = []
        self.folders_listbox = None  # set in create_widgets
        self.multi_mode = False
        self.remaining_folders = []
        self.multi_success = True
        
        # Other instance variables
        self.worker = None
        self.stop_event = threading.Event()
        self.rate_limit_lock = threading.Lock()
        self.rate_limit_queue = Queue()
        
        # Create widgets and load settings
        self.setup_theme_and_fonts()
        # Build a Material 3-style top app bar
        try:
            self._m3_header = ttk.Frame(self)
            self._m3_header.pack(fill='x', side='top')
            hdr = getattr(self, 'm3_palette', None)
            if isinstance(hdr, dict):
                try:
                    self._m3_header.configure(style='M3Header.TFrame')
                except Exception:
                    pass
            self._m3_title = ttk.Label(self._m3_header, text='Telegram Uploader', style='M3Header.TLabel')
            self._m3_title.pack(anchor='w', padx=12, pady=(10, 8))
        except Exception:
            pass
        # Build application menubar for managing credentials and uploads
        try:
            self._menubar = tk.Menu(self)
            self.config(menu=self._menubar)
            # Credentials menu
            self._menu_credentials = tk.Menu(self._menubar, tearoff=0)
            self._menubar.add_cascade(label='Credentials', menu=self._menu_credentials)
            self._menu_credentials.add_command(label='Add Bot Token…', command=self.menu_add_token)
            self._menu_credentials.add_command(label='Remove Current Token', command=self.menu_remove_current_token)
            self._menu_credentials.add_separator()
            self._menu_credentials.add_command(label='Add Channel…', command=self.menu_add_channel)
            self._menu_credentials.add_command(label='Remove Current Channel', command=self.menu_remove_current_channel)
            # Upload menu (so we can hide toolbar buttons from UI)
            self._menu_upload = tk.Menu(self._menubar, tearoff=0)
            self._menubar.add_cascade(label='Upload', menu=self._menu_upload)
            self._menu_upload.add_command(label='Start Upload', command=self.start_upload)
            self._menu_upload.add_command(label='Stop Upload', command=self.stop_upload)
            self._menu_upload.add_separator()
            self._menu_upload.add_command(label='Open Logs Folder', command=self.open_logs_directory)
        except Exception:
            pass
        self.create_widgets()
        self.load_settings()
        self.load_tokens_store()
        # In compact mode, collapse advanced section by default to save vertical space
        try:
            if self.compact_mode and hasattr(self, 'sec_advanced'):
                self.sec_advanced.collapse()
        except Exception:
            pass

        # Try to bring window to front so users see it immediately
        try:
            self.update_idletasks()
            self.deiconify()
            self.lift()
            # Temporarily set topmost to force focus, then revert
            self.attributes('-topmost', True)
            self.after(200, lambda: self.attributes('-topmost', False))
        except Exception:
            pass

    def setup_theme_and_fonts(self) -> None:
        """Apply a modern ttk theme (if available) and set default fonts to Lato with fallbacks."""
        try:
            # Try ttkbootstrap first for rounded, modern widgets
            theme_applied = False
            if ttkb is not None:
                try:
                    style = ttkb.Style(self)
                    # A clean theme with subtle roundness
                    style.theme_use('flatly')
                    theme_applied = True
                    self.using_ttkbootstrap = True
                except Exception:
                    self.using_ttkbootstrap = False
                    theme_applied = False
            else:
                self.using_ttkbootstrap = False

            # Try to ensure ttkthemes exists, then pick a theme (if ttkbootstrap not used)
            try:
                ensure_ttkthemes_available()
            except Exception:
                pass

            if not theme_applied and 'ThemedStyle' in globals() and ThemedStyle is not None:
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
                    # Prefer native aqua on macOS for better rounded system buttons
                    theme_order = ('aqua', 'clam', 'alt', 'default') if platform.system() == 'Darwin' else ('clam', 'alt', 'default')
                    for t in theme_order:
                        if t in style.theme_names():
                            style.theme_use(t)
                            break
                except Exception:
                    pass

            # Choose font families:
            # - Base UI font prefers Lato, then Helvetica/Arial, else Tk default
            # - Heading font prefers Dubai (if installed) for section headers; falls back to base
            try:
                families = {f.lower() for f in tkfont.families()}
            except Exception:
                families = set()
            # Base family
            if 'lato' in families:
                base_family = 'Lato'
            elif 'helvetica' in families:
                base_family = 'Helvetica'
            elif 'arial' in families:
                base_family = 'Arial'
            else:
                base_family = None
            # Heading family (section labels)
            if 'dubai' in families:
                heading_family = 'Dubai'
            else:
                heading_family = base_family
            # Slightly smaller default font in compact mode
            size = 10 if getattr(self, 'compact_mode', False) else 12
            if base_family:
                try:
                    # Set default for all Tk widgets (covers tk.Text, etc.)
                    self.option_add('*Font', f'{base_family} {size}')
                except Exception:
                    pass
                try:
                    # Set default for ttk widgets
                    style.configure('.', font=(base_family, size))
                    # Slightly tighter paddings in compact mode
                    if getattr(self, 'compact_mode', False):
                        style.configure('TButton', padding=(6, 3))
                        style.configure('TLabel', padding=1)
                        style.configure('TEntry', padding=3)
                    else:
                        style.configure('TButton', padding=(8, 4))
                        style.configure('TLabel', padding=2)
                        style.configure('TEntry', padding=4)
                except Exception:
                    pass
            # Apply heading font for LabelFrame titles and an optional header label style
            try:
                if heading_family:
                    style.configure('TLabelframe.Label', font=(heading_family, size + 1))
                    style.configure('Heading.TLabel', font=(heading_family, size + 1))
            except Exception:
                pass
            # Accent and Danger button styles (dark/light adaptive)
            try:
                dark = detect_dark_mode()
                if dark:
                    accent_fg = '#FFFFFF'
                    accent_bg = '#D0BCFF'  # M3 dark primary
                    accent_bg_active = '#B69DF8'
                    danger_bg = '#F2B8B5'
                    danger_active = '#E59893'
                else:
                    accent_fg = '#FFFFFF'
                    accent_bg = '#6750A4'  # M3 light primary
                    accent_bg_active = '#7F67BE'
                    danger_bg = '#B3261E'
                    danger_active = '#8C1D18'

                # If ttkbootstrap is present, rely on its rounded look and palette; otherwise color our styles
                if getattr(self, 'using_ttkbootstrap', False):
                    # Map our semantic names to ttkbootstrap built-ins by convention
                    # We'll set styles when creating buttons by choosing appropriate bootstyles if needed.
                    pass
                else:
                    # Use system/default foreground to avoid invisible text if theme has light fg on light bg
                    try:
                        default_fg = style.lookup('TButton', 'foreground', default='black') or 'black'
                    except Exception:
                        default_fg = 'black'
                    style.configure('Accent.TButton', foreground=accent_fg, background=accent_bg, relief='flat')
                    style.map('Accent.TButton', background=[('active', accent_bg_active), ('pressed', accent_bg_active)])
                    style.configure('Danger.TButton', foreground='#FFFFFF' if not dark else '#201A1A', background=danger_bg, relief='flat')
                    style.map('Danger.TButton', background=[('active', danger_active), ('pressed', danger_active)])
            except Exception:
                pass
            # Expose style on instance for later tweaks
            self.style = style

            # Apply Material 3-inspired styles over the base theme
            try:
                pal = apply_material3_styles(self, self.style)
                self.m3_palette = pal
                # Header styles
                try:
                    self.style.configure('M3Header.TFrame', background=pal['primary'])
                    self.style.configure('M3Header.TLabel', background=pal['primary'], foreground=pal['onPrimary'], font=('Helvetica', 14, 'bold'))
                except Exception:
                    pass
            except Exception:
                self.m3_palette = None
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
            'channel_name': self.channel_name_var.get().strip(),
            'as_document': self.as_doc_var.get(),
            'no_album': self.no_album_var.get(),
            'delay': self.delay_var.get(),
            'jitter': self.jitter_var.get(),
            'resume': self.resume_var.get(),
            'delete_after_upload': self.delete_after_upload_var.get(),
            'include_link': self.include_link_var.get(),
            'channel_link': self.channel_link_var.get().strip(),
            'use_custom_caption': self.use_custom_caption_var.get(),
            'custom_caption': (self.custom_caption_text.get('1.0', 'end-1c').strip() if self.custom_caption_text is not None else self.custom_caption_var.get().strip()),
            'folders': list(self.folders_list)
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
                if settings.get('channel_name'):
                    self.channel_name_var.set(settings['channel_name'])
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
                if 'folders' in settings and isinstance(settings['folders'], list):
                    try:
                        self.folders_list = [str(p) for p in settings['folders']]
                        if self.folders_listbox is not None:
                            self.folders_listbox.delete(0, 'end')
                            for p in self.folders_list:
                                self.folders_listbox.insert('end', p)
                    except Exception:
                        pass
        except Exception as e:
            messagebox.showwarning('Warning', f'Could not load settings: {e}')

    def create_widgets(self):
        pad = getattr(self, 'base_pad', 8)
        # Fixed log height: 5 rows (user request) regardless of compact mode
        log_height = 5

        # Top frame (folder, token, channel selectors)
        frm_top = ttk.Frame(self)
        frm_top.pack(fill='x', padx=pad, pady=pad)
        for i in range(3):
            frm_top.columnconfigure(i, weight=0)
        frm_top.columnconfigure(1, weight=1)

        ttk.Label(frm_top, text='Folder:').grid(row=0, column=0, sticky='w')
        self.entry_folder = ttk.Entry(frm_top, textvariable=self.folder_var)
        self.entry_folder.grid(row=0, column=1, sticky='ew')
        self.make_button(frm_top, text='Browse', command=self.browse_folder, role='secondary').grid(row=0, column=2, padx=6)

        ttk.Label(frm_top, text='Saved Token:').grid(row=1, column=0, sticky='w', pady=(6,0))
        self.token_combo = ttk.Combobox(frm_top, state='readonly', textvariable=self.token_var)
        self.token_combo.grid(row=1, column=1, sticky='ew', pady=(6,0))
        self.token_combo.bind('<<ComboboxSelected>>', lambda e: self.select_saved_token())
        self.make_button(frm_top, text='Save Current', command=self.save_current_token, role='secondary').grid(row=1, column=2, padx=6, pady=(6,0))

        ttk.Label(frm_top, text='Saved Channels:').grid(row=2, column=0, sticky='w', pady=(4,0))
        self.channel_combo = ttk.Combobox(frm_top, state='readonly')
        self.channel_combo.grid(row=2, column=1, sticky='ew', pady=(4,0))
        self.channel_combo.bind('<<ComboboxSelected>>', lambda e: self.select_saved_channel())
        self.make_button(frm_top, text='Save Channel', command=self.save_current_channel, role='secondary').grid(row=2, column=2, padx=6, pady=(4,0))

        # Link Bot Token - Channel Name (Channel ID)
        frm_link = ttk.Frame(self)
        frm_link.pack(fill='x', padx=pad, pady=(0,6))
        frm_link.columnconfigure(1, weight=1)
        ttk.Label(frm_link, text='Channel Name (ID):').grid(row=0, column=0, sticky='w')
        self.entry_channel_name = ttk.Entry(frm_link, textvariable=self.channel_name_var)
        self.entry_channel_name.grid(row=0, column=1, sticky='ew')
        ttk.Label(frm_link, text='Channel ID:').grid(row=1, column=0, sticky='w', pady=(4,0))
        self.entry_chat = ttk.Entry(frm_link, textvariable=self.chat_var)
        self.entry_chat.grid(row=1, column=1, sticky='ew', pady=(4,0))

        # Folders section
        self.sec_folders = CollapsibleSection(self, title='Folders Selected', initially_collapsed=self.compact_mode)
        self.sec_folders.pack(fill='x', padx=pad, pady=(0,6))
        frm_multi = self.sec_folders.content
        frm_multi.columnconfigure(0, weight=1)
        # Multi-selection listbox
        self.folders_listbox = tk.Listbox(
            frm_multi,
            height=4 if not self.compact_mode else 3,
            selectmode='extended',
            exportselection=False
        )
        self.folders_listbox.grid(row=0, column=0, sticky='ew')
        try:
            pal = getattr(self, 'm3_palette', None)
            if isinstance(pal, dict):
                self.folders_listbox.configure(
                    bg=pal['surface'], fg=pal['onSurface'],
                    selectbackground=pal['secondaryContainer'],
                    selectforeground=pal['onSecondaryContainer'],
                    highlightthickness=0, bd=0
                )
        except Exception:
            pass
        sb_folders = ttk.Scrollbar(frm_multi, orient='vertical', command=self.folders_listbox.yview)
        sb_folders.grid(row=0, column=1, sticky='ns')
        self.folders_listbox.configure(yscrollcommand=sb_folders.set)
        try:
            Tooltip(self.folders_listbox, 'Tip: Use Cmd(⌘)-click to toggle selection, Shift-click for ranges.')
        except Exception:
            pass
        btns_multi = ttk.Frame(frm_multi)
        btns_multi.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4,0))
        self.make_button(btns_multi, text='Add Folder', command=self.add_folder, role='primary').pack(side='left')
        self.make_button(btns_multi, text='Add Folders (Recursive…) ', command=self.add_folders_recursive_dialog, role='secondary').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Add Subfolders…', command=self.add_subfolders, role='secondary').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Remove Selected', command=self.remove_selected_folders, role='danger').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Clear', command=self.clear_folders, role='outline').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Keep Selected Only', command=self.keep_selected_only, role='outline').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Select All', command=self.select_all_folders, role='outline').pack(side='left', padx=(6,0))
        self.make_button(btns_multi, text='Invert Selection', command=self.invert_selection, role='outline').pack(side='left', padx=(6,0))

        # Minimal link option row (append channel link in captions)
        link_frame = ttk.Frame(self)
        link_frame.pack(fill='x', padx=pad)
        ttk.Label(link_frame, text='Channel link (appended to caption):').grid(row=0, column=0, sticky='w')
        channel_link_entry = ttk.Entry(link_frame, textvariable=self.channel_link_var)
        channel_link_entry.grid(row=0, column=1, sticky='ew')
        ttk.Checkbutton(link_frame, text='Append link', variable=self.include_link_var, command=self.update_caption_preview).grid(row=0, column=2, sticky='w', padx=(8,0))
        try:
            Tooltip(channel_link_entry, 'Channel link added to caption when enabled')
        except Exception:
            pass

        # Actions row (minimal surface: Start / Stop)
        actions = ttk.Frame(self)
        actions.pack(fill='x', padx=pad, pady=(6, 0))
        self.btn_start = self.make_button(actions, text='Start Upload', command=self.start_upload, role='primary')
        self.btn_start.pack(side='left')
        self.btn_stop = self.make_button(actions, text='Stop', command=self.stop_upload, role='danger', state='disabled')
        self.btn_stop.pack(side='left', padx=(6, 0))
        # Progress bar (compact)
        self.progress = ttk.Progressbar(actions, orient='horizontal', mode='determinate', length=160)
        self.progress.pack(side='left', padx=(12,0))
        try:
            self.progress['maximum'] = 100
            self.progress['value'] = 0
        except Exception:
            pass

        # Logs section
        frm_log = ttk.Frame(self)
        # Keep horizontal fill, but avoid vertical expansion so height stays ~5 lines
        frm_log.pack(fill='x', expand=False, padx=pad, pady=(6, pad))
        frm_log.columnconfigure(0, weight=1)
        frm_log.rowconfigure(0, weight=1)
        log_scroll_y = ttk.Scrollbar(frm_log, orient='vertical')
        log_scroll_y.grid(row=0, column=1, sticky='ns')
        self.log_widget = tk.Text(frm_log, wrap='word', height=log_height, yscrollcommand=log_scroll_y.set)
        self.log_widget.grid(row=0, column=0, sticky='nsew')
        log_scroll_y.config(command=self.log_widget.yview)
        self.log_widget.configure(state='disabled')
        try:
            pal = getattr(self, 'm3_palette', None)
            if isinstance(pal, dict):
                self.log_widget.configure(bg=pal['surface'], fg=pal['onSurface'], insertbackground=pal['onSurface'])
        except Exception:
            pass

        # Status bar
        status_bar = ttk.Frame(self)
        status_bar.pack(fill='x', padx=pad, pady=(0,6))
        ttk.Label(status_bar, textvariable=self.status_var).pack(side='left')
        ttk.Label(status_bar, textvariable=self.elapsed_var).pack(side='right')

        # Advanced options section
        self.sec_advanced = CollapsibleSection(self, title='Advanced Options', initially_collapsed=self.compact_mode)
        self.sec_advanced.pack(fill='x', padx=pad, pady=(4,0))
        frm_adv = self.sec_advanced.content
        for i in range(3):
            frm_adv.columnconfigure(i, weight=1)
        ttk.Label(frm_adv, text='Delay (s):').grid(row=0, column=0, sticky='w')
        ttk.Entry(frm_adv, textvariable=self.delay_var, width=8).grid(row=0, column=0, sticky='e')
        ttk.Label(frm_adv, text='Jitter (s):').grid(row=0, column=1, sticky='w')
        ttk.Entry(frm_adv, textvariable=self.jitter_var, width=8).grid(row=0, column=1, sticky='e')
        ttk.Label(frm_adv, text='Parallel uploads:').grid(row=0, column=2, sticky='w', padx=(12,0))
        ttk.Entry(frm_adv, textvariable=self.max_workers_var, width=3).grid(row=0, column=2, sticky='e')
        ttk.Checkbutton(frm_adv, text='Resume previous run if progress found', variable=self.resume_var).grid(row=1, column=0, columnspan=2, sticky='w', pady=(4,0))
        ttk.Checkbutton(frm_adv, text='Move files to .uploaded after upload (safer)', variable=self.delete_after_upload_var).grid(row=1, column=2, sticky='w', pady=(4,0))
        ttk.Checkbutton(frm_adv, text='Skip bot token validation (use with caution)', variable=self.skip_validate_var).grid(row=2, column=0, columnspan=3, sticky='w', pady=(4,0))

        # Start elapsed timer and initial caption preview computation
        self.after(1000, self._tick_elapsed)
        self.update_caption_preview()
    def make_button(self, parent, text: str, command=None, role: str = 'secondary', **kwargs):
        """Create a nicely-styled (rounded when possible) button.
        role: 'primary' | 'danger' | 'info' | 'secondary' | 'outline'
        """
        try:
            if getattr(self, 'using_ttkbootstrap', False) and ttkb is not None:
                # Map semantic role to ttkbootstrap bootstyle
                role_map = {
                    'primary': 'primary',
                    'danger': 'danger',
                    'info': 'info',
                    'secondary': 'secondary',
                    'outline': 'secondary-outline'
                }
                bootstyle = role_map.get(role, 'secondary')
                return ttkb.Button(parent, text=text, command=command, bootstyle=bootstyle, **kwargs)
            else:
                # Fallback to ttk styles configured earlier
                style = None
                if role == 'primary':
                    style = 'Accent.TButton'
                elif role == 'danger':
                    style = 'Danger.TButton'
                # info/secondary/outline fall back to default style
                if style:
                    return ttk.Button(parent, text=text, command=command, style=style, **kwargs)
                return ttk.Button(parent, text=text, command=command, **kwargs)
        except Exception:
            # Last-resort plain tk.Button
            return tk.Button(parent, text=text, command=command, **kwargs)
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
                channels_raw = data.get('channels', [])
                # Deduplicate while preserving order
                def dedupe(seq):
                    seen = set()
                    out = []
                    for item in seq:
                        item = str(item).strip()
                        if not item:
                            continue
                        if item not in seen:
                            seen.add(item)
                            out.append(item)
                    return out
                self.tokens_list = dedupe(tokens)[:50]
                # Normalize channels to list of dicts {id,name}
                norm = []
                seen_ids = set()
                for ch in channels_raw:
                    if isinstance(ch, dict):
                        cid = str(ch.get('id', '')).strip()
                        cname = str(ch.get('name', '')).strip() or cid
                    else:
                        cid = str(ch).strip()
                        cname = cid
                    if cid and cid not in seen_ids:
                        seen_ids.add(cid)
                        norm.append({'id': cid, 'name': cname})
                self.channels_list = norm[:50]
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
                labels = []
                for c in self.channels_list:
                    if isinstance(c, dict):
                        labels.append(f"{c.get('name','')} — {c.get('id','')}")
                    else:
                        labels.append(str(c))
                self.channel_combo['values'] = labels
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

    # ---- Menubar token/channel management ----
    def menu_add_token(self):
        try:
            tok = simpledialog.askstring('Add Bot Token', 'Enter bot token:', parent=self)
        except Exception:
            tok = None
        if not tok:
            return
        self.token_var.set(tok.strip())
        self.save_current_token()

    def menu_remove_current_token(self):
        tok = self.token_var.get().strip()
        if not tok:
            return
        try:
            self.tokens_list = [t for t in self.tokens_list if t != tok]
            self.save_tokens_store()
            self.update_tokens_channels_combos()
        except Exception:
            pass

    def save_current_channel(self):
        chan_id = self.chat_var.get().strip()
        if not chan_id:
            return
        chan_name = self.channel_name_var.get().strip() or chan_id
        # Dedupe by id
        try:
            existing_ids = {c['id'] for c in self.channels_list}
        except Exception:
            existing_ids = set()
        if chan_id not in existing_ids:
            self.channels_list.insert(0, {'id': chan_id, 'name': chan_name})
        else:
            for c in self.channels_list:
                try:
                    if c.get('id') == chan_id:
                        c['name'] = chan_name
                        break
                except Exception:
                    pass
        self.channels_list = self.channels_list[:50]
        self.save_tokens_store()
        self.update_tokens_channels_combos()

    def menu_add_channel(self):
        try:
            cid = simpledialog.askstring('Add Channel', 'Enter channel ID (@username or -100...):', parent=self)
            if not cid:
                return
            name = simpledialog.askstring('Channel Name', 'Friendly name (optional):', parent=self)
        except Exception:
            cid = None
            name = None
        if not cid:
            return
        self.chat_var.set(cid.strip())
        if name:
            self.channel_name_var.set(name.strip())
        self.save_current_channel()

    def menu_remove_current_channel(self):
        cid = self.chat_var.get().strip()
        if not cid:
            return
        try:
            self.channels_list = [c for c in self.channels_list if not (isinstance(c, dict) and c.get('id') == cid)]
            self.save_tokens_store()
            self.update_tokens_channels_combos()
        except Exception:
            pass

    # ---- Captions menu actions ----
    def menu_set_custom_caption(self):
        try:
            txt = simpledialog.askstring('Custom Caption', 'Enter custom caption text (used as base):', parent=self, initialvalue=self.custom_caption_var.get())
        except Exception:
            txt = None
        if txt is None:
            return
        txt = txt.strip()
        self.use_custom_caption_var.set(True)
        self.custom_caption_var.set(txt)
        if self.custom_caption_text is not None:
            try:
                self.custom_caption_text.delete('1.0', 'end')
                self.custom_caption_text.insert('1.0', txt)
            except Exception:
                pass
        self.update_caption_preview()

    def menu_clear_custom_caption(self):
        self.use_custom_caption_var.set(False)
        self.custom_caption_var.set('')
        if self.custom_caption_text is not None:
            try:
                self.custom_caption_text.delete('1.0', 'end')
            except Exception:
                pass
        self.update_caption_preview()

    def menu_toggle_append_link(self):
        try:
            self.include_link_var.set(not bool(self.include_link_var.get()))
        except Exception:
            pass
        self.update_caption_preview()

    def menu_set_channel_link(self):
        try:
            cur = self.channel_link_var.get().strip()
            link = simpledialog.askstring('Channel Link', 'Set the channel link to append:', parent=self, initialvalue=cur)
        except Exception:
            link = None
        if link is None:
            return
        self.channel_link_var.set(link.strip())
        self.update_caption_preview()

    def update_caption_preview(self):
        """Recompute caption preview using current GUI settings."""
        if self.use_custom_caption_var.get():
            if self.custom_caption_text is not None:
                base = self.custom_caption_text.get('1.0', 'end-1c').strip()
            else:
                base = self.custom_caption_var.get().strip()
        else:
            if self.folders_list:
                try:
                    base = Path(self.folders_list[0]).name
                except Exception:
                    base = ''
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
        if not sel:
            return
        # expected format "Name — ID"; fallback to raw
        try:
            if '—' in sel:
                name_part, id_part = sel.split('—', 1)
                self.channel_name_var.set(name_part.strip())
                self.chat_var.set(id_part.strip())
            else:
                self.chat_var.set(sel)
        except Exception:
            self.chat_var.set(sel)

    # ---- Multi-folder helpers ----
    def add_folder(self):
        path = filedialog.askdirectory()
        if path:
            if path not in self.folders_list:
                self.folders_list.append(path)
                if self.folders_listbox is not None:
                    self.folders_listbox.insert('end', path)
            # keep single-folder field synced for non-multi code paths
            self.folder_var.set(path)
            self.update_caption_preview()

    def remove_selected_folders(self):
        if self.folders_listbox is None:
            return
        try:
            sel = list(self.folders_listbox.curselection())
            sel.sort(reverse=True)
            for idx in sel:
                try:
                    self.folders_list.pop(idx)
                except Exception:
                    pass
                self.folders_listbox.delete(idx)
        except Exception:
            pass
        self.update_caption_preview()

    def clear_folders(self):
        self.folders_list.clear()
        if self.folders_listbox is not None:
            self.folders_listbox.delete(0, 'end')
        self.update_caption_preview()

    def select_all_folders(self):
        """Select all entries in the folders listbox."""
        if self.folders_listbox is None:
            return
        try:
            self.folders_listbox.selection_set(0, 'end')
        except Exception:
            pass

    def invert_selection(self):
        """Invert the current selection in the folders listbox."""
        if self.folders_listbox is None:
            return
        try:
            count = self.folders_listbox.size()
            current = set(self.folders_listbox.curselection())
            self.folders_listbox.selection_clear(0, 'end')
            for i in range(count):
                if i not in current:
                    self.folders_listbox.selection_set(i)
        except Exception:
            pass

    def add_subfolders(self):
        """Add immediate subfolders of a chosen parent directory to the folders list.
        Filters to subfolders that contain at least one supported media file.
        """
        parent = filedialog.askdirectory(title='Choose parent directory')
        if not parent:
            return
        parent_path = Path(parent)
        if not parent_path.exists() or not parent_path.is_dir():
            messagebox.showerror('Invalid folder', 'Selected path is not a directory')
            return
        added = 0
        try:
            for child in sorted(parent_path.iterdir()):
                try:
                    if not child.is_dir():
                        continue
                    # Check for at least one media file inside (non-recursive)
                    has_media = False
                    for p in child.iterdir():
                        if p.is_file() and (is_image(p) or is_video(p)):
                            has_media = True
                            break
                    if not has_media:
                        continue
                    s = str(child)
                    if s not in self.folders_list:
                        self.folders_list.append(s)
                        if self.folders_listbox is not None:
                            self.folders_listbox.insert('end', s)
                        added += 1
                except Exception:
                    continue
        except Exception as e:
            messagebox.showerror('Error', f'Could not list subfolders: {e}')
            return
        # Sync single-folder field for consistency
        if added and self.folders_list:
            self.folder_var.set(self.folders_list[0])
        self.append_log(f'➕ Added {added} subfolders from "{parent_path.name}"')
        self.update_caption_preview()


    def keep_selected_only(self):
        """Keep only the currently selected entries in the folders list.
        Useful when you've bulk-added and want to trim down to a few.
        """
        if self.folders_listbox is None:
            return
        try:
            sel = list(self.folders_listbox.curselection())
            if not sel:
                return
            # Build new list in the order of current selection
            keep = []
            for idx in sel:
                try:
                    keep.append(self.folders_list[idx])
                except Exception:
                    pass
            self.folders_list = keep
            # Rebuild listbox
            self.folders_listbox.delete(0, 'end')
            for p in self.folders_list:
                self.folders_listbox.insert('end', p)
            # Sync single-folder field and caption preview
            if self.folders_list:
                self.folder_var.set(self.folders_list[0])
        except Exception:
            pass
        self.update_caption_preview()

    def add_folders_recursive_dialog(self):
        """Show a small dialog to choose parent folder, depth, and media-type filters, then add folders recursively."""
        dlg = tk.Toplevel(self)
        dlg.title('Recursive Folder Add')
        dlg.transient(self)
        dlg.grab_set()
        dlg.resizable(False, False)
        frm = ttk.Frame(dlg, padding=8)
        frm.pack(fill='both', expand=True)

        parent_var = tk.StringVar()
        depth_var = tk.IntVar(value=0)  # 0 means unlimited
        include_images_var = tk.BooleanVar(value=True)
        include_videos_var = tk.BooleanVar(value=True)
        min_files_var = tk.IntVar(value=1)
        include_patterns_var = tk.StringVar(value='')  # comma-separated patterns or regexes
        exclude_patterns_var = tk.StringVar(value='')
        use_regex_var = tk.BooleanVar(value=False)
        case_sensitive_var = tk.BooleanVar(value=False)

        def browse_parent():
            p = filedialog.askdirectory(title='Choose parent folder')
            if p:
                parent_var.set(p)

        ttk.Label(frm, text='Parent folder:').grid(row=0, column=0, sticky='w')
        ent_parent = ttk.Entry(frm, textvariable=parent_var, width=40)
        ent_parent.grid(row=0, column=1, sticky='w')
        ttk.Button(frm, text='Browse…', command=browse_parent).grid(row=0, column=2, padx=(6,0))

        ttk.Label(frm, text='Max depth (0 = unlimited):').grid(row=1, column=0, sticky='w', pady=(6,0))
        ttk.Entry(frm, textvariable=depth_var, width=6).grid(row=1, column=1, sticky='w', pady=(6,0))

        ttk.Label(frm, text='Min media files per folder:').grid(row=2, column=0, sticky='w', pady=(6,0))
        ttk.Entry(frm, textvariable=min_files_var, width=6).grid(row=2, column=1, sticky='w', pady=(6,0))

        ttk.Checkbutton(frm, text='Include image folders', variable=include_images_var).grid(row=3, column=0, columnspan=2, sticky='w', pady=(6,0))
        ttk.Checkbutton(frm, text='Include video folders', variable=include_videos_var).grid(row=4, column=0, columnspan=2, sticky='w')

        ttk.Label(frm, text='Include name patterns (comma):').grid(row=5, column=0, sticky='w', pady=(6,0))
        ttk.Entry(frm, textvariable=include_patterns_var, width=40).grid(row=5, column=1, columnspan=2, sticky='w', pady=(6,0))
        ttk.Label(frm, text='Exclude name patterns (comma):').grid(row=6, column=0, sticky='w')
        ttk.Entry(frm, textvariable=exclude_patterns_var, width=40).grid(row=6, column=1, columnspan=2, sticky='w')
        ttk.Checkbutton(frm, text='Use regex', variable=use_regex_var).grid(row=7, column=0, sticky='w', pady=(4,0))
        ttk.Checkbutton(frm, text='Case sensitive', variable=case_sensitive_var).grid(row=7, column=1, sticky='w', pady=(4,0))

        status_var = tk.StringVar(value='')
        ttk.Label(frm, textvariable=status_var, foreground='#555').grid(row=8, column=0, columnspan=3, sticky='w', pady=(4,0))

        btn_bar = ttk.Frame(frm)
        btn_bar.grid(row=9, column=0, columnspan=3, pady=(10,0), sticky='e')
        ttk.Button(btn_bar, text='Cancel', command=lambda: dlg.destroy()).pack(side='right')

        def run_scan():
            parent_path = Path(parent_var.get().strip())
            if not parent_path.exists() or not parent_path.is_dir():
                status_var.set('Invalid parent folder')
                return
            max_depth = depth_var.get()
            include_images = include_images_var.get()
            include_videos = include_videos_var.get()
            min_files = max(1, min_files_var.get())
            inc_raw = include_patterns_var.get().strip()
            exc_raw = exclude_patterns_var.get().strip()
            use_regex = use_regex_var.get()
            case_sensitive = case_sensitive_var.get()

            def split_patterns(raw: str):
                if not raw:
                    return []
                return [p.strip() for p in raw.split(',') if p.strip()]

            include_list = split_patterns(inc_raw)
            exclude_list = split_patterns(exc_raw)

            # Precompile regexes if needed
            inc_regexes = []
            exc_regexes = []
            flags = 0 if case_sensitive else re.IGNORECASE
            if use_regex:
                for pat in include_list:
                    try:
                        inc_regexes.append(re.compile(pat, flags))
                    except Exception:
                        pass
                for pat in exclude_list:
                    try:
                        exc_regexes.append(re.compile(pat, flags))
                    except Exception:
                        pass

            added = 0
            scanned = 0

            def should_scan(depth):
                return max_depth <= 0 or depth <= max_depth

            # Walk using BFS to respect depth easily
            queue = [(parent_path, 0)]
            while queue:
                current, depth = queue.pop(0)
                if not should_scan(depth):
                    continue
                try:
                    # Count media files in current folder
                    media_count = 0
                    has_image = False
                    has_video = False
                    for item in current.iterdir():
                        if item.is_file():
                            if is_image(item):
                                has_image = True
                                media_count += 1
                            elif is_video(item):
                                has_video = True
                                media_count += 1
                        elif item.is_dir():
                            # Enqueue child directory for further scanning
                            queue.append((item, depth + 1))
                    scanned += 1
                    # Apply media-type & count filters first
                    if media_count >= min_files and ((include_images and has_image) or (include_videos and has_video)):
                        folder_name = current.name
                        passes = True
                        # Name include/exclude logic
                        if use_regex:
                            if inc_regexes:
                                passes = any(r.search(folder_name) for r in inc_regexes)
                            if passes and exc_regexes:
                                if any(r.search(folder_name) for r in exc_regexes):
                                    passes = False
                        else:
                            # Simple substring (case-insensitive unless case_sensitive)
                            test_name = folder_name if case_sensitive else folder_name.lower()
                            if include_list:
                                inc_norm = [p if case_sensitive else p.lower() for p in include_list]
                                passes = any(p in test_name for p in inc_norm)
                            if passes and exclude_list:
                                exc_norm = [p if case_sensitive else p.lower() for p in exclude_list]
                                if any(p in test_name for p in exc_norm):
                                    passes = False
                        if passes:
                            s = str(current)
                            if s not in self.folders_list:
                                self.folders_list.append(s)
                                if self.folders_listbox is not None:
                                    self.folders_listbox.insert('end', s)
                                added += 1
                except Exception:
                    continue

            if added and self.folders_list:
                self.folder_var.set(self.folders_list[0])
            self.append_log(f'🔍 Recursive add: scanned {scanned} folder(s), added {added}.')
            self.update_caption_preview()
            dlg.destroy()

        ttk.Button(btn_bar, text='Scan & Add', command=run_scan).pack(side='right', padx=(0,6))

        # Center dialog relative to parent
        try:
            self.update_idletasks()
            x = self.winfo_rootx() + (self.winfo_width() // 2) - 200
            y = self.winfo_rooty() + (self.winfo_height() // 2) - 140
            dlg.geometry(f"520x460+{x}+{y}")
        except Exception:
            pass



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
                # cap history to avoid UI slowdown
                if len(self.log_lines) > 5000:
                    self.log_lines = self.log_lines[-4000:]
            except Exception:
                pass
            # Incremental display in GUI for real-time feel
            try:
                self.log_widget.configure(state='normal')
                flt = (self.filter_var.get() or '').strip().lower()
                if not flt:
                    # Fast path: just append the new line
                    self.log_widget.insert('end', log_message)
                else:
                    # Append only if it matches the current filter; full rebuild happens on filter change
                    if flt in log_message.lower():
                        self.log_widget.insert('end', log_message)
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
                # Progress bar is optional in simplified UI
                try:
                    if hasattr(self, 'progress') and self.progress is not None:
                        self.progress['maximum'] = t
                        self.progress['value'] = min(s, t)
                except Exception:
                    pass

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

    def _http_post_json(self, url: str, data: dict):
        """Post JSON/FORM to Telegram with requests if available, else urllib. Returns parsed JSON or None."""
        try:
            if requests is not None:
                r = requests.post(url, data=data, timeout=20)
                try:
                    return r.json()
                except Exception:
                    return None
            else:
                # urllib fallback
                import urllib.parse
                body = urllib.parse.urlencode(data).encode('utf-8')
                req = urllib.request.Request(url, data=body)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    raw = resp.read().decode('utf-8')
                    try:
                        return json.loads(raw)
                    except Exception:
                        return None
        except Exception as e:
            try:
                self.append_log(f'_http_post_json error: {e}')
            except Exception:
                pass
            return None

    def send_test_message(self):
        """Try sending a small test message to the configured channel to verify permissions and chat id."""
        token = self.token_var.get().strip()
        chat_id = self.chat_var.get().strip()
        if not token or not chat_id:
            messagebox.showerror('Missing', 'Please enter Bot Token and Channel ID first')
            return
        # Best-effort message
        text = 'Test message from Telegram Uploader GUI'
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.append_log('Sending test message to channel...')
        j = self._http_post_json(api_url, {'chat_id': chat_id, 'text': text})
        if isinstance(j, dict) and j.get('ok'):
            messagebox.showinfo('Success', 'Test message sent. Check your channel.')
            self.append_log('✅ Test message sent successfully')
        else:
            # Try to show a helpful hint
            desc = ''
            try:
                if isinstance(j, dict):
                    desc = json.dumps(j)
            except Exception:
                pass
            self.append_log(f'❌ Test message failed: {desc or "unknown error"}')
            hint = 'Ensure the bot is added as an Admin in your channel with Post Messages permission. '
            hint += 'Also verify the Channel ID is correct (use @username for public channels or the numeric id starting with -100 for private/supergroups).'
            messagebox.showerror('Failed', f'Could not send test message.\n\n{hint}\n\nDetails: {desc}')

    def start_upload(self):
        # Determine initial folder (single or multi selection)
        folder = self.folder_var.get().strip()
        if self.folders_list:
            # Initialize multi-mode sequence if more than one folder
            if len(self.folders_list) > 1:
                self.multi_mode = True
                # Remaining folders queue (Path objects)
                self.remaining_folders = [Path(p) for p in self.folders_list]
                folder_path_obj = self.remaining_folders.pop(0)
                folder = str(folder_path_obj)
            else:
                self.multi_mode = False
                self.remaining_folders = []
                folder = self.folders_list[0]
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
        try:
            self.btn_start.config(state='disabled')
            self.btn_stop.config(state='normal')
        except Exception:
            pass
        self.stop_event.clear()
        
        # Clear log & in-memory list
        self.log_widget.configure(state='normal')
        self.log_widget.delete('1.0', 'end')
        self.log_widget.configure(state='disabled')
        self.log_lines.clear()
        
        # Reset status & progress
        self.status_var.set('Starting upload...')
        try:
            if hasattr(self, 'progress') and self.progress:
                self.progress['value'] = 0
                self.progress['maximum'] = 100
        except Exception:
            pass

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

    def _start_next_folder(self):
        """Internal helper to start uploading the next folder in multi-mode."""
        if not self.multi_mode or not self.remaining_folders:
            # Nothing left
            self.multi_mode = False
            return
        if self.stop_event.is_set():
            self.multi_mode = False
            return
        next_folder = self.remaining_folders.pop(0)
        self.folder_var.set(str(next_folder))
        self.append_log(f'➡️ Moving to next folder: {next_folder.name}')
        # Reuse existing token/chat settings
        # Reset status & progress
        self.status_var.set('Starting upload...')
        try:
            if hasattr(self, 'progress') and self.progress:
                self.progress['value'] = 0
                self.progress['maximum'] = 100
        except Exception:
            pass
        # Create new worker
        self.worker = UploadWorker(
            folder=next_folder,
            token=self.token_var.get().strip(),
            chat_id=self.chat_var.get().strip(),
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
        self.start_time_epoch = time.time()
        self.worker.start()

    def stop_upload(self):
        if messagebox.askyesno('Stop', 'Stop the upload? This will save progress and stop.'):
            self.stop_event.set()
            try:
                self.btn_stop.config(state='disabled')
            except Exception:
                pass
            self.append_log('Stop requested — waiting for worker to finish...')

    def upload_done(self, success: bool):
        self.append_log('Upload finished' if success else 'Upload stopped / failed')
        # If multi-mode and success and folders remain, chain next
        if self.multi_mode and success and not self.stop_event.is_set() and self.remaining_folders:
            self._start_next_folder()
            return
        # Finalize
        try:
            self.btn_start.config(state='normal')
            self.btn_stop.config(state='disabled')
        except Exception:
            pass
        self.start_time_epoch = None
        self.multi_mode = False
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
        # Backwards compatibility: map old toggle to collapsing the advanced section.
        try:
            if hasattr(self, 'sec_advanced') and self.sec_advanced:
                if self.sec_advanced._collapsed:
                    self.sec_advanced.expand()
                else:
                    self.sec_advanced.collapse()
        except Exception:
            pass

    def expand_all_sections(self):
        """Expand all collapsible option sections (folders, caption)."""
        try:
            if hasattr(self, 'sec_folders') and self.sec_folders:
                self.sec_folders.expand()
        except Exception:
            pass
        try:
            if hasattr(self, 'sec_caption') and self.sec_caption:
                self.sec_caption.expand()
        except Exception:
            pass
        try:
            if hasattr(self, 'sec_advanced') and self.sec_advanced:
                self.sec_advanced.expand()
        except Exception:
            pass

    def collapse_all_sections(self):
        """Collapse all collapsible option sections (folders, caption)."""
        try:
            if hasattr(self, 'sec_folders') and self.sec_folders:
                self.sec_folders.collapse()
        except Exception:
            pass
        try:
            if hasattr(self, 'sec_caption') and self.sec_caption:
                self.sec_caption.collapse()
        except Exception:
            pass
        try:
            if hasattr(self, 'sec_advanced') and self.sec_advanced:
                self.sec_advanced.collapse()
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
