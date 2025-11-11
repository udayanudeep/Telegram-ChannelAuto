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
import json
import time
import random
import threading
import re
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
import urllib.request
import urllib.error

try:
    import requests
except Exception:
    requests = None

# Supported extensions (used for logging and checks)
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}
VIDEO_EXTS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}

# ----------------- Helper functions (networking & upload) -----------------

def is_image(p: Path) -> bool:
    return p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}


def is_video(p: Path) -> bool:
    return p.suffix.lower() in {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}


class UploadWorker(threading.Thread):
    def __init__(self, folder: Path, token: str, chat_id: str, as_document: bool,
                 no_album: bool, delay: float, jitter: float, resume: bool,
                 delete_after_upload: bool, max_workers: int,
                 progress_callback, log_callback, done_callback, stop_event: threading.Event):
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

    def get_caption(self, file_path: Path) -> str:
        try:
            # Convert path to string and replace backslashes with forward slashes
            path_str = str(file_path).replace('\\', '/')
            # Find the position after the base path
            if self.base_path in path_str:
                # Get everything after the base path
                relative_path = path_str.split(self.base_path, 1)[1]
                # Get everything before the first '/' (the account name)
                account_name = relative_path.split('/', 1)[0]
                return account_name
            return file_path.name
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
            try:
                resp = self.session.post(url, data=data, files=files, timeout=120)
            except requests.RequestException as e:
                backoff = self.BASE_BACKOFF * (2 ** (attempt - 1)) + random.random()
                self.log(f'Network error ({context}) attempt {attempt}/{self.MAX_RETRIES}:', e, f'backoff={backoff:.1f}s')
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

            # success or non-json
            return j if j is not None else resp

        raise RuntimeError(f'Failed after {self.MAX_RETRIES} attempts for {context}')

    def send_media_group(self, chat_id: str, paths: List[Path]):
        if not paths:
            return None
        
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
            return None
            
        if self.as_document:
            # documents cannot be sent in media group reliably
            results = []
            for p in valid_paths:
                r = self.send_single_by_type(chat_id, p)
                results.append(r)
                # handle move/delete in a centralized helper
                self._post_upload_action(p)
                if self.stop_event.is_set():
                    break
                time.sleep(self.delay + random.uniform(0, self.jitter))
            return results

        url = f"{self.api_url}/sendMediaGroup"
        media = []
        files = {}
        for i, p in enumerate(paths[:10]):
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
            j = self.request_with_retries(url, data=data, files=files, context=f'media_group {paths[0].name}')
            return j
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
                        # Handle media group upload
                        try:
                            self.send_media_group(self.chat_id, batch)
                            msg = f'✅ Sent album {idx}/{batch_count} ({len(batch)} items) first={batch[0].name}'
                            self.log(msg)
                            for p in batch:
                                    files_processed.append(p.name)
                                    # move/delete in central helper
                                    self._post_upload_action(p)
                                    # update shared counter and UI per-file
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
                            # Reset session on error
                            if "429" in str(e):
                                self.init_session()
                            self.log(f'⚠️ Album upload failed (batch {idx}), trying individual files')
                            # Fall back to individual file upload
                            for p in batch:
                                if self.stop_event.is_set():
                                    break
                                try:
                                    self.send_single_by_type(self.chat_id, p)
                                    self.log(f'✅ Sent {p.name}')
                                    files_processed.append(p.name)
                                    # move/delete in central helper
                                    self._post_upload_action(p)
                                    # update shared counter and UI per-file
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
                                    # Reset session on rate limit
                                    if "429" in str(e2):
                                        self.init_session()
                                time.sleep(max(0, self.delay + random.uniform(0, self.jitter)))
                    else:
                        # Handle individual file upload
                        for p in batch:
                            if self.stop_event.is_set():
                                break
                            try:
                                self.send_single_by_type(self.chat_id, p)
                                self.log(f'✅ Sent {p.name}')
                                files_processed.append(p.name)
                                # move/delete in central helper
                                self._post_upload_action(p)
                                # update shared counter and UI per-file
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
                                # Reset session on rate limit
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
        self.geometry('800x600')
        self.config_file = Path.home() / '.telegram_uploader_config.json'
        self.logs_dir = Path.home() / '.telegram_uploader' / 'logs'
        
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
        self.status_var = tk.StringVar(value='Idle')
        
        # Other instance variables
        self.worker = None
        self.stop_event = threading.Event()
        self.rate_limit_lock = threading.Lock()
        self.rate_limit_queue = Queue()
        
        # Create widgets and load settings
        self.create_widgets()
        self.load_settings()

    def save_settings(self):
        settings = {
            'token': self.token_var.get().strip(),
            'chat_id': self.chat_var.get().strip(),
            'as_document': self.as_doc_var.get(),
            'no_album': self.no_album_var.get(),
            'delay': self.delay_var.get(),
            'jitter': self.jitter_var.get(),
            'resume': self.resume_var.get(),
            'delete_after_upload': self.delete_after_upload_var.get()
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
        except Exception as e:
            messagebox.showwarning('Warning', f'Could not load settings: {e}')

    def create_widgets(self):
        pad = 8
        frm_top = ttk.Frame(self)
        frm_top.pack(fill='x', padx=pad, pady=pad)

        # Folder selection
        ttk.Label(frm_top, text='Folder:').grid(row=0, column=0, sticky='w')
        self.folder_var = tk.StringVar()
        self.entry_folder = ttk.Entry(frm_top, textvariable=self.folder_var, width=60)
        self.entry_folder.grid(row=0, column=1, sticky='w')
        ttk.Button(frm_top, text='Browse', command=self.browse_folder).grid(row=0, column=2, padx=6)

        # Bot token
        ttk.Label(frm_top, text='Bot Token:').grid(row=1, column=0, sticky='w', pady=(6,0))
        self.token_var = tk.StringVar()
        self.entry_token = ttk.Entry(frm_top, textvariable=self.token_var, width=60, show='*')
        self.entry_token.grid(row=1, column=1, sticky='w')
        ttk.Button(frm_top, text='Show', command=self.toggle_token).grid(row=1, column=2, padx=6)

        # Channel ID
        ttk.Label(frm_top, text='Channel ID:').grid(row=2, column=0, sticky='w', pady=(6,0))
        self.chat_var = tk.StringVar()
        self.entry_chat = ttk.Entry(frm_top, textvariable=self.chat_var, width=60)
        self.entry_chat.grid(row=2, column=1, sticky='w')

        # Options
        frm_opts = ttk.Frame(self)
        frm_opts.pack(fill='x', padx=pad)
        self.as_doc_var = tk.BooleanVar(value=False)
        self.no_album_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm_opts, text='Send as document (preserve quality)', variable=self.as_doc_var).grid(row=0, column=0, sticky='w')
        ttk.Checkbutton(frm_opts, text='No album (send files individually)', variable=self.no_album_var).grid(row=0, column=1, sticky='w', padx=12)

        ttk.Label(frm_opts, text='Delay (s):').grid(row=1, column=0, sticky='w', pady=(6,0))
        self.delay_var = tk.DoubleVar(value=1.0)
        ttk.Entry(frm_opts, textvariable=self.delay_var, width=8).grid(row=1, column=0, sticky='e')

        ttk.Label(frm_opts, text='Jitter (s):').grid(row=1, column=1, sticky='w', pady=(6,0))
        self.jitter_var = tk.DoubleVar(value=0.4)
        ttk.Entry(frm_opts, textvariable=self.jitter_var, width=8).grid(row=1, column=1, sticky='e')

        ttk.Label(frm_opts, text='Parallel uploads:').grid(row=1, column=2, sticky='w', pady=(6,0), padx=(12,0))
        ttk.Entry(frm_opts, textvariable=self.max_workers_var, width=3).grid(row=1, column=2, sticky='e')

        self.resume_var = tk.BooleanVar(value=True)
        # Enable delete-after-upload by default as requested
        self.delete_after_upload_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opts, text='Resume previous run if progress found', variable=self.resume_var).grid(row=2, column=0, sticky='w', pady=(6,0))
        ttk.Checkbutton(frm_opts, text='Move files to .uploaded after upload (safer)', variable=self.delete_after_upload_var).grid(row=2, column=1, sticky='w', pady=(6,0))
        ttk.Checkbutton(frm_opts, text='Skip bot token validation (use with caution)', variable=self.skip_validate_var).grid(row=2, column=2, sticky='w', pady=(6,0), padx=(12,0))

        # Progress bar and stats
        frm_prog = ttk.Frame(self)
        frm_prog.pack(fill='x', padx=pad, pady=(8,0))
        self.progress = ttk.Progressbar(frm_prog, mode='determinate', length=100)
        self.progress.pack(fill='x', expand=True)
        ttk.Label(frm_prog, textvariable=self.status_var).pack(anchor='w', pady=(4,0))

        # Buttons
        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(fill='x', padx=pad, pady=(6,0))
        self.btn_start = ttk.Button(frm_buttons, text='Start Upload', command=self.start_upload)
        self.btn_start.pack(side='left')
        self.btn_stop = ttk.Button(frm_buttons, text='Stop', command=self.stop_upload, state='disabled')
        self.btn_stop.pack(side='left', padx=6)

        ttk.Button(frm_buttons, text='Open Progress File', command=self.open_progress_file).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Clear Progress File', command=self.clear_progress_file).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Open Logs', command=self.open_logs_directory).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Undo Last Move', command=self.undo_last_move).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Test Connection', command=self.test_connection).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Save Settings', command=self.save_settings).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Save As...', command=self.save_settings_as).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Load Settings...', command=self.load_settings_from_file).pack(side='left', padx=6)

        # Log output
        frm_log = ttk.Frame(self)
        frm_log.pack(fill='both', expand=True, padx=pad, pady=(6, pad))
        self.log_widget = tk.Text(frm_log, wrap='word', height=15)
        self.log_widget.pack(fill='both', expand=True)
        self.log_widget.configure(state='disabled')

    def browse_folder(self):
        path = filedialog.askdirectory()
        if path:
            self.folder_var.set(path)



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

            # Display in GUI
            try:
                self.log_widget.configure(state='normal')
                self.log_widget.insert('end', log_message)
                self.log_widget.see('end')
                self.log_widget.configure(state='disabled')
            except tk.TclError:
                # GUI is closed or not available
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

        if requests is None:
            messagebox.showerror('Missing dependency', 'The requests library is not installed. Install it in your environment: python -m pip install requests')
            return

        # Reset UI state
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.stop_event.clear()
        
        # Clear log
        self.log_widget.configure(state='normal')
        self.log_widget.delete('1.0', 'end')
        self.log_widget.configure(state='disabled')
        
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
            stop_event=self.stop_event
        )
        self.append_log('Starting upload...')
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


if __name__ == '__main__':
    app = App()
    app.mainloop()
