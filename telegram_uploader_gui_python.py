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

Install requests (recommended inside a venv):
    python3 -m venv venv
    source venv/bin/activate
    python -m pip install requests

Run:
    python3 gui_instagram_uploader.py

"""

import os
import json
import time
import random
import threading
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List

try:
    import requests
except Exception:
    requests = None

# ----------------- Helper functions (networking & upload) -----------------

def is_image(p: Path) -> bool:
    return p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.tiff', '.bmp', '.heic'}


def is_video(p: Path) -> bool:
    return p.suffix.lower() in {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.flv', '.3gp', '.ts', '.wmv', '.m4v'}


class UploadWorker(threading.Thread):
    def __init__(self, folder: Path, token: str, chat_id: str, as_document: bool,
                 no_album: bool, delay: float, jitter: float, resume: bool,
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
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.done_callback = done_callback
        self.stop_event = stop_event
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

    def request_with_retries(self, url: str, data=None, files=None, context=''):
        attempt = 0
        while attempt < self.MAX_RETRIES and not self.stop_event.is_set():
            attempt += 1
            try:
                resp = requests.post(url, data=data, files=files, timeout=120)
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
                self.log(f'Supported image types: {", ".join(sorted(is_image(Path("test.txt"))._field_defaults))}')
                self.log(f'Supported video types: {", ".join(sorted(is_video(Path("test.txt"))._field_defaults))}')
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

            for idx, batch in enumerate(batches, start=1):
                if self.stop_event.is_set():
                    self.log('Stop requested. Saving progress...')
                    self.save_progress(uploaded)
                    self.done_callback(False)
                    return

                try:
                    if len(batch) > 1 and not self.as_document:
                        # try media group
                        res = None
                        try:
                            res = self.send_media_group(self.chat_id, batch)
                            self.log(f'✅ Sent album {idx}/{batch_count} ({len(batch)} items) first={batch[0].name}')
                            for p in batch:
                                uploaded.append(p.name)
                                files_sent += 1
                        except Exception as e:
                            self.log(f'⚠️ Media group failed for batch {idx}:', e)
                            # fallback to individual sends
                            for p in batch:
                                try:
                                    self.send_single_by_type(self.chat_id, p)
                                    self.log(f'✅ Sent {p.name}')
                                    uploaded.append(p.name)
                                    files_sent += 1
                                    if self.stop_event.is_set():
                                        break
                                    time.sleep(self.delay + random.uniform(0, self.jitter))
                                except Exception as e2:
                                    self.log(f'❌ Failed to send {p.name}:', e2)
                                    # if 429 will be handled inside request_with_retries
                                    # continue to next file (or we could retry)
                                    continue
                    else:
                        for p in batch:
                            if self.stop_event.is_set():
                                break
                            try:
                                self.send_single_by_type(self.chat_id, p)
                                self.log(f'✅ Sent {p.name}')
                                uploaded.append(p.name)
                                files_sent += 1
                            except Exception as e:
                                self.log(f'❌ Failed to send {p.name}:', e)
                            time.sleep(self.delay + random.uniform(0, self.jitter))

                except Exception as e:
                    self.log(f'❌ Batch {idx} failed with exception:', e)
                    # back off and retry by reinserting batch
                    backoff = self.BASE_BACKOFF * 4 + random.uniform(0,2)
                    self.log('Backing off for', f'{backoff:.1f}s')
                    time.sleep(backoff)
                    batches.insert(idx, batch)
                    continue

                # persist progress
                self.save_progress(uploaded)

                # progress callback
                elapsed = time.time() - start_time
                avg_per_file = elapsed / files_sent if files_sent else 0.001
                remaining_files = total - files_sent
                eta = remaining_files * avg_per_file
                self.progress_callback(files_sent, total, eta)

                # delay between batches
                time.sleep(self.delay + random.uniform(0, self.jitter))

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


# ----------------- GUI -----------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('Telegram Uploader — GUI')
        self.geometry('800x600')
        self.create_widgets()
        self.worker = None
        self.stop_event = threading.Event()

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

        self.resume_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_opts, text='Resume previous run if progress found', variable=self.resume_var).grid(row=2, column=0, sticky='w', pady=(6,0))

        # Buttons
        frm_buttons = ttk.Frame(self)
        frm_buttons.pack(fill='x', padx=pad, pady=(6,0))
        self.btn_start = ttk.Button(frm_buttons, text='Start Upload', command=self.start_upload)
        self.btn_start.pack(side='left')
        self.btn_stop = ttk.Button(frm_buttons, text='Stop', command=self.stop_upload, state='disabled')
        self.btn_stop.pack(side='left', padx=6)

        ttk.Button(frm_buttons, text='Open Progress File', command=self.open_progress_file).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Clear Progress File', command=self.clear_progress_file).pack(side='left', padx=6)
        ttk.Button(frm_buttons, text='Test Connection', command=self.test_connection).pack(side='left', padx=6)

        # Progress bar and stats
        frm_prog = ttk.Frame(self)
        frm_prog.pack(fill='x', padx=pad, pady=(8,0))
        self.progress = ttk.Progressbar(frm_prog, mode='determinate')
        self.progress.pack(fill='x')
        self.status_var = tk.StringVar(value='Idle')
        ttk.Label(frm_prog, textvariable=self.status_var).pack(anchor='w')

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

    def append_log(self, text):
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', f'[{timestamp}] {text}\n')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')

    def progress_cb(self, sent, total, eta_seconds):
        self.progress['maximum'] = total
        self.progress['value'] = sent
        eta_text = f"ETA: {self.seconds_to_hms(eta_seconds)}" if eta_seconds and eta_seconds > 1 else 'ETA: <1s'
        self.status_var.set(f'Sent {sent}/{total} — {eta_text}')

    def seconds_to_hms(self, s):
        try:
            s = int(s)
        except Exception:
            return '0s'
        m, sec = divmod(s, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m}m {sec}s"
        if m:
            return f"{m}m {sec}s"
        return f"{sec}s"

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

    def validate_token(self, token: str) -> bool:
        if not token or len(token.split(':')) != 2:
            return False
        try:
            url = f"https://api.telegram.org/bot{token}/getMe"
            response = requests.get(url, timeout=10)
            return response.json().get('ok', False)
        except Exception:
            return False

    def test_connection(self):
        token = self.token_var.get().strip()
        if not token:
            messagebox.showerror('Error', 'Please enter a bot token')
            return
        
        self.btn_start.config(state='disabled')
        try:
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
            
        if not self.validate_token(token):
            messagebox.showerror('Error', 'Invalid bot token')
            return

        fpath = Path(folder)
        if not fpath.exists() or not fpath.is_dir():
            messagebox.showerror('Invalid folder', 'Folder invalid')
            return

        if requests is None:
            messagebox.showerror('Missing dependency', 'The requests library is not installed. Install it in your environment: python -m pip install requests')
            return

        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.stop_event.clear()
        self.log_widget.configure(state='normal')
        self.log_widget.delete('1.0', 'end')
        self.log_widget.configure(state='disabled')

        self.worker = UploadWorker(
            folder=fpath,
            token=token,
            chat_id=chat_id,
            as_document=self.as_doc_var.get(),
            no_album=self.no_album_var.get(),
            delay=self.delay_var.get(),
            jitter=self.jitter_var.get(),
            resume=self.resume_var.get(),
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


if __name__ == '__main__':
    app = App()
    app.mainloop()
