import json
import os
import shutil
import sys
import time
import zipfile
import signal
import socket
from collections import defaultdict
from contextlib import contextmanager
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Thread, Event
from typing import Dict, List, Tuple, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# -----------------------
# Config (env-overridable)
# -----------------------

FHIR_BASE_URL = os.getenv("FHIR_BASE_URL", "http://hapi:8080/fhir").rstrip("/")
SYNTHEA_ZIP_URL = os.getenv(
    "SYNTHEA_ZIP_URL",
    "https://synthetichealth.github.io/synthea-sample-data/downloads/latest/synthea_sample_data_fhir_latest.zip",
)
WORK_DIR = os.getenv("WORK_DIR", "/tmp/synthea")
NDJSON_DIR = os.path.join(WORK_DIR, "ndjson")

# HTTP server (serves NDJSON to HAPI)
HTTP_PORT = int(os.getenv("UPLOADER_CONTAINER_PORT", "8001"))
HTTP_HOST = os.getenv("LOCAL_ADDRESS", "0.0.0.0")  # bind inside the container
PUBLIC_HOSTNAME = os.getenv("PUBLIC_HOSTNAME", "uploader")  # how HAPI sees this service

# Timeouts & polling
CONNECT_TIMEOUT = float(os.getenv("CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.getenv("READ_TIMEOUT", "120"))
PIPED_REQ_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
DOWNLOAD_CHUNK = int(os.getenv("DOWNLOAD_CHUNK_BYTES", str(1024 * 256)))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "5"))
POLL_BACKOFF = float(os.getenv("POLL_BACKOFF", "1.25"))
POLL_MAX_INTERVAL = float(os.getenv("POLL_MAX_INTERVAL", "60"))
POLL_MAX_SECONDS = float(os.getenv("POLL_MAX_SECONDS", "0"))  # 0 = no limit

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
USE_JSON_LOG = os.getenv("JSON_LOGS", "1").strip() not in ("0", "false", "False")


# -----------------------
# Lightweight logger
# -----------------------
LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40}
CUR_LEVEL = LEVELS.get(LOG_LEVEL, 20)

def _emit(level: str, msg: str, **fields):
    if LEVELS[level] < CUR_LEVEL:
        return
    if USE_JSON_LOG:
        rec = {"level": level, "msg": msg, "ts": int(time.time()), **fields}
        sys.stdout.write(json.dumps(rec) + "\n")
    else:
        extras = " ".join(f"{k}={v}" for k, v in fields.items())
        prefix = f"[{level:<5}]"
        sys.stdout.write(f"{prefix} {msg} {extras}\n")
    sys.stdout.flush()

def log_debug(msg, **f): _emit("DEBUG", msg, **f)
def log_info(msg, **f): _emit("INFO", msg, **f)
def log_warn(msg, **f): _emit("WARN", msg, **f)
def log_error(msg, **f): _emit("ERROR", msg, **f)


# -----------------------
# HTTP session w/ retries
# -----------------------
def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5,
        read=5,
        connect=5,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s

SESSION = build_session()
STOP_EVENT = Event()


# -----------------------
# Utilities
# -----------------------
def human_bytes(n: Optional[int]) -> str:
    if n is None:
        return "unknown"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def resolve_ip(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except Exception:
        return "unresolved"

@contextmanager
def time_block(label: str, **fields):
    start = time.time()
    log_info(f"{label} - start", **fields)
    try:
        yield
    finally:
        dur = time.time() - start
        log_info(f"{label} - done", duration_sec=f"{dur:.2f}", **fields)


# -----------------------
# Steps
# -----------------------
def download_and_extract() -> Tuple[str, str]:
    os.makedirs(WORK_DIR, exist_ok=True)
    zip_path = os.path.join(WORK_DIR, "synthea.zip")

    with time_block("download_zip", url=SYNTHEA_ZIP_URL, dest=zip_path):
        with SESSION.get(SYNTHEA_ZIP_URL, stream=True, timeout=PIPED_REQ_TIMEOUT) as r:
            r.raise_for_status()
            total = r.headers.get("Content-Length")
            total_i = int(total) if total is not None else None
            seen = 0
            last_pct = -1
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=DOWNLOAD_CHUNK):
                    if STOP_EVENT.is_set():  # allow graceful shutdown
                        log_warn("download_cancelled")
                        return zip_path, WORK_DIR
                    if chunk:
                        f.write(chunk)
                        seen += len(chunk)
                        if total_i:
                            pct = int((seen / total_i) * 100)
                            if pct != last_pct and pct % 5 == 0:
                                log_info("download_progress", bytes=seen, total=total_i, human=f"{human_bytes(seen)}/{human_bytes(total_i)}", pct=pct)
                                last_pct = pct

    with time_block("extract_zip", path=zip_path, out_dir=WORK_DIR):
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad_file = zf.testzip()
            if bad_file:
                raise zipfile.BadZipFile(f"Corrupt file in archive: {bad_file}")
            zf.extractall(WORK_DIR)

    return zip_path, WORK_DIR


def convert_to_ndjson() -> Dict[str, int]:
    os.makedirs(NDJSON_DIR, exist_ok=True)

    resources_by_type: Dict[str, List[dict]] = defaultdict(list)
    files_scanned = 0

    with time_block("scan_bundles", work_dir=WORK_DIR):
        for root, _, files in os.walk(WORK_DIR):
            for file in files:
                if not file.endswith(".json"):
                    continue
                bundle_path = os.path.join(root, file)
                files_scanned += 1
                try:
                    with open(bundle_path, "r") as f:
                        bundle = json.load(f)
                except Exception as e:
                    log_warn("skip_invalid_json", file=bundle_path, error=str(e))
                    continue

                if bundle.get("resourceType") == "Bundle":
                    for entry in bundle.get("entry", []):
                        res = entry.get("resource")
                        if res and isinstance(res, dict):
                            rtype = res.get("resourceType")
                            if rtype:
                                resources_by_type[rtype].append(res)

    counts = {k: len(v) for k, v in resources_by_type.items()}
    log_info("bundle_scan_summary", files_scanned=files_scanned, resource_types=len(counts), counts_sample=dict(list(counts.items())[:8]))

    with time_block("write_ndjson", ndjson_dir=NDJSON_DIR, types=len(counts)):
        for rtype, resources in resources_by_type.items():
            path = os.path.join(NDJSON_DIR, f"{rtype}.ndjson")
            with open(path, "w") as f:
                for res in resources:
                    f.write(json.dumps(res) + "\n")
            log_info("ndjson_written", rtype=rtype, records=len(resources), path=path)

    return counts


class QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log_debug("http_access", message=fmt % args)

    def do_GET(self):
        if self.path.endswith('.ndjson'):
            self.send_response(200)
            self.send_header("Content-type", "application/fhir+ndjson")
            self.end_headers()
            try:
                with open(self.translate_path(self.path), 'rb') as f:
                    shutil.copyfileobj(f, self.wfile)
            except BrokenPipeError:
                log_warn("http_broken_pipe", path=self.path)
            except Exception as e:
                log_error("http_serve_error", path=self.path, error=str(e))
        else:
            super().do_GET()

def start_http_server() -> Tuple[HTTPServer, Thread]:
    os.chdir(NDJSON_DIR)
    server = HTTPServer((HTTP_HOST, HTTP_PORT), QuietHTTPRequestHandler)

    def _serve():
        # Python’s HTTPServer doesn’t support shutdown event logs; we add ours.
        log_info("http_server_listening",
                 bind_host=HTTP_HOST,
                 bind_port=HTTP_PORT,
                 bind_ip=resolve_ip(HTTP_HOST),
                 public_host=PUBLIC_HOSTNAME,
                 public_url_base=f"http://{PUBLIC_HOSTNAME}:{HTTP_PORT}/")
        server.serve_forever()

    thread = Thread(target=_serve, daemon=True)
    thread.start()
    return server, thread


def list_ndjson_inputs() -> List[Tuple[str, str]]:
    # returns list of (rtype, url)
    inputs = []
    for file in sorted(os.listdir(NDJSON_DIR)):
        if file.endswith(".ndjson"):
            rtype = file.split(".")[0]
            url = f"http://{PUBLIC_HOSTNAME}:{HTTP_PORT}/{file}"
            inputs.append((rtype, url))
    if not inputs:
        raise RuntimeError("No NDJSON files found to import.")
    log_info("ndjson_inputs_ready", count=len(inputs))
    for rtype, url in inputs[:12]:
        log_debug("ndjson_input_sample", rtype=rtype, url=url)
    return inputs


def trigger_bulk_import() -> str:
    inputs = [
        {
            "name": "input",
            "part": [
                {"name": "type", "valueCode": rtype},
                {"name": "url", "valueUri": url},
            ],
        }
        for rtype, url in list_ndjson_inputs()
    ]

    payload = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "inputFormat", "valueCode": "application/fhir+ndjson"},
            # Uncomment/adjust if you need different behaviors:
            # {"name": "mode", "valueCode": "incremental"},
            {"name": "mode", "valueCode": "initialize"},
        ] + inputs,
    }

    with time_block("start_bulk_import", fhir_base=FHIR_BASE_URL):
        headers = {"Prefer": "respond-async", "Content-Type": "application/fhir+json"}
        resp = SESSION.post(
            f"{FHIR_BASE_URL}/$import",
            json=payload,
            headers=headers,
            timeout=PIPED_REQ_TIMEOUT,
        )
        log_info("import_response", status=resp.status_code, headers=dict(resp.headers))
        if resp.status_code != 202:
            # Try to log server response body (OperationOutcome, etc.)
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise RuntimeError(f"Failed to start import: {body}")

        status_url = resp.headers.get("Content-Location")
        if not status_url:
            raise RuntimeError("Import started (202) but missing Content-Location header.")
        log_info("import_started", status_url=status_url)
        return status_url


def poll_status(status_url: str):
    interval = POLL_INTERVAL
    deadline = time.time() + POLL_MAX_SECONDS if POLL_MAX_SECONDS > 0 else None
    tries = 0

    with time_block("poll_import_status", status_url=status_url):
        while True:
            if STOP_EVENT.is_set():
                log_warn("poll_cancelled")
                return

            tries += 1
            try:
                resp = SESSION.get(status_url, timeout=PIPED_REQ_TIMEOUT)
            except Exception as e:
                log_warn("poll_error", error=str(e), try_num=tries)
                time.sleep(min(interval, POLL_MAX_INTERVAL))
                interval = min(interval * POLL_BACKOFF, POLL_MAX_INTERVAL)
                continue

            log_debug("poll_tick", code=resp.status_code, try_num=tries)

            if resp.status_code == 202:
                # Still processing
                try:
                    progress = resp.json()
                except Exception:
                    progress = {"raw": resp.text[:500]}
                log_info("import_in_progress", details=progress)
            elif resp.status_code == 200:
                # Done – success or partial failures will be in body
                try:
                    data = resp.json()
                except Exception:
                    data = {"raw": resp.text[:2000]}
                log_info("import_complete", result_summary=data)
                return
            else:
                # HAPI might return OperationOutcome or error text
                try:
                    body = resp.json()
                except Exception:
                    body = resp.text
                raise RuntimeError(f"Import failed (HTTP {resp.status_code}): {body}")

            # check deadline
            if deadline and time.time() > deadline:
                raise TimeoutError(f"Polling exceeded {POLL_MAX_SECONDS} seconds.")

            time.sleep(min(interval, POLL_MAX_INTERVAL))
            interval = min(interval * POLL_BACKOFF, POLL_MAX_INTERVAL)


# -----------------------
# Shutdown handling
# -----------------------
def _graceful_shutdown(server: Optional[HTTPServer]):
    STOP_EVENT.set()
    if server:
        try:
            server.shutdown()
        except Exception as e:
            log_warn("server_shutdown_error", error=str(e))
    log_info("cleanup_complete")


def _install_signal_handlers(server_ref):
    def handler(signum, frame):
        log_warn("signal_received", signum=signum)
        _graceful_shutdown(server_ref["server"])
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# -----------------------
# Main
# -----------------------
def main():
    log_info("startup_config",
             fhir_base_url=FHIR_BASE_URL,
             synthea_url=SYNTHEA_ZIP_URL,
             work_dir=WORK_DIR,
             ndjson_dir=NDJSON_DIR,
             http_bind=f"{HTTP_HOST}:{HTTP_PORT}",
             public_host=PUBLIC_HOSTNAME,
             log_level=LOG_LEVEL)

    server = None
    server_ref = {"server": None}
    _install_signal_handlers(server_ref)

    try:
        download_and_extract()
        counts = convert_to_ndjson()
        log_info("ndjson_counts", **counts)

        server, thread = start_http_server()
        server_ref["server"] = server

        status_url = trigger_bulk_import()
        poll_status(status_url)

    except Exception as e:
        log_error("fatal_error", error=str(e))
        raise
    finally:
        _graceful_shutdown(server)


if __name__ == "__main__":
    main()
