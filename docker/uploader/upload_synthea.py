#!/usr/bin/env python3
"""
- Seeds practitioners & hospitals first so inline match URLs resolve.
    - Needs to be done before patient bundles, as patient bundles
      use inline match URLs (e.g., Practitioner?identifier=...) which
      HAPI FHIR only resolves if those resources already exist, preventing HAPI-1091 errors.
    - Patient bundles need to have Practitioner and Hospital resources loaded first to reference!
- Then uploads all remaining bundles using asyncio for high performance.
- Retries files that failed with HAPI-1091 after seeding.
- Skips Synthea run metadata files that start with a timestamp prefix
  like 'YYYY_MM_DDTHH_MM_SSZ_...' (e.g. 2025_09_15T05_30_55Z_111_Massachusetts_<uuidish>.json).
"""
from fileinput import filename
import os, re, time, argparse, sys, asyncio, json
from typing import List, Tuple, Optional, Dict, Any
import aiohttp
from dotenv import load_dotenv

load_dotenv()

HAPI_MATCH_ERR = "Invalid match URL"

# Matches Synthea metadata filenames like:
# 2025_09_15T05_30_55Z_111_Massachusetts_318b28b2_cde8_4b7e_b364_e5aced5e0db7.json
# Key signal is the leading timestamp "YYYY_MM_DDTHH_MM_SSZ_"
_METADATA_PREFIX_RE = re.compile(r"^\d{4}_\d{2}_\d{2}T\d{2}_\d{2}_\d{2}Z_")

def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()

async def send_fhir(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    body: bytes,
    token: Optional[str] = None,
    timeout: int = 120,
) -> Tuple[int, bytes, Optional[str]]:
    """Send a FHIR HTTP request with standard headers and fully consume the response.

    Returning the HTTP status, raw response body, and declared content type allows callers to
    log errors without leaking connections from the aiohttp pool. Fully reading the payload is
    essential for high concurrency because it makes the connection immediately reusable.
    """
    headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with session.request(method.upper(), url, data=body, headers=headers, timeout=timeout_cfg) as resp:
        payload = await resp.read()
        return resp.status, payload, resp.headers.get("Content-Type")

def _transform_bundle_to_update_create(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure a Bundle is a transaction using PUT per-entry with explicit id (update-as-create).

    - For each entry with a resource having resourceType and id, set request to PUT on [type]/[id].
    - Set bundle.type to 'transaction'.
    """
    if not isinstance(bundle, dict):
        return bundle
    if bundle.get("resourceType") != "Bundle":
        return bundle
    entries = bundle.get("entry") or []
    changed = False
    for e in entries:
        if not isinstance(e, dict):
            continue
        res = e.get("resource")
        if not isinstance(res, dict):
            continue
        rtype = res.get("resourceType")
        rid = res.get("id")
        if rtype and rid:
            # Force PUT to [type]/[id]
            e["request"] = {"method": "PUT", "url": f"{rtype}/{rid}"}
            changed = True
    if changed:
        bundle["type"] = "transaction"
    return bundle

def _prepare_request_for_content(base_url: str, raw: bytes) -> Tuple[str, str, bytes]:
    """Return (method, url, body) for upload-as-create.

    - If Bundle: convert entries to PUT and POST the transaction Bundle to base.
    - If single resource with id: PUT to [base]/[type]/[id].
    - Else: POST body to base unchanged.
    """
    base = base_url.rstrip("/")
    try:
        data = json.loads(raw)
    except Exception:
        return ("POST", base, raw)

    if isinstance(data, dict) and data.get("resourceType") == "Bundle":
        transformed = _transform_bundle_to_update_create(data)
        body = json.dumps(transformed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return ("POST", base, body)

    if isinstance(data, dict) and data.get("resourceType") and data.get("id"):
        rtype = data["resourceType"]
        rid = data["id"]
        url = f"{base}/{rtype}/{rid}"
        # Server SHALL ignore meta.versionId/lastUpdated on update; send as-is
        return ("PUT", url, raw)

    return ("POST", base, raw)

def is_seed_file(name: str) -> bool:
    return bool(re.search(r"^(practitionerInformation|hospitalInformation).+\.json$", name, re.IGNORECASE))

def is_metadata_file(name: str) -> bool:
    # Fast check for the Synthea run metadata naming convention
    # e.g., 2025_09_15T05_30_55Z_*_.json
    return name.lower().endswith(".json") and bool(_METADATA_PREFIX_RE.match(name))

def plan_files(root: str):
    all_json = [f for f in os.listdir(root) if f.lower().endswith(".json")]
    # Filter out Synthea run metadata files up front
    meta = sorted([f for f in all_json if is_metadata_file(f)])
    non_meta = [f for f in all_json if f not in meta]

    seeds = sorted([f for f in non_meta if is_seed_file(f)])
    rest = sorted([f for f in non_meta if f not in seeds])
    return seeds, rest, meta

def looks_like_hapi_1091(text: str) -> bool:
    return HAPI_MATCH_ERR in text or "HAPI-1091" in text

def _to_printable(body: bytes, content_type: Optional[str], limit: int = 300) -> str:
    if not body:
        return ""
    charset = "utf-8"
    if content_type:
        match = re.search(r"charset=([^;]+)", content_type, re.IGNORECASE)
        if match:
            charset = match.group(1).strip()
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    text = text.replace("\n", " ")
    if len(text) > limit:
        text = text[:limit]
    return text

async def upload_file_worker(session: aiohttp.ClientSession, sem: asyncio.Semaphore, base_url: str, root_dir: str, filename: str, token: Optional[str]) -> Tuple[str, Optional[str]]:
    path = os.path.join(root_dir, filename)
    async with sem: # Acquire semaphore to limit concurrency
        try:
            if hasattr(asyncio, "to_thread"):
                raw = await asyncio.to_thread(read_bytes, path)
            else:  # Python < 3.9 fallback
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(None, read_bytes, path)
            method, url, body = _prepare_request_for_content(base_url, raw)
            status, resp_body, resp_ct = await send_fhir(session, method, url, body, token=token)
            if 200 <= status < 300:
                return filename, None
            preview = _to_printable(resp_body, resp_ct)
            return filename, f"status={status} body={preview}"
        except aiohttp.ClientError as e:
            return filename, str(e)
        except Exception as e:
            return filename, f"An unexpected error occurred: {e}"

async def phase_upload_seeds(session: aiohttp.ClientSession, base_url: str, root: str, files: List[str], token: Optional[str] = None) -> List[str]:
    failures = []
    if not files: return failures
    print("─"*20); print("Uploading seed files...")
    for name in files:
        path = os.path.join(root, name)
        print(f"[seed] Uploading {name} ...")
        try:
            raw = read_bytes(path)
            method, url, body = _prepare_request_for_content(base_url, raw)
            status, resp_body, resp_ct = await send_fhir(session, method, url, body, token=token)
            if 200 <= status < 300:
                print(f"  ✓ Success ({status})")
            else:
                preview = _to_printable(resp_body, resp_ct)
                print(f"  ✗ Failed ({status}): {preview}")
                failures.append(name)
        except aiohttp.ClientError as e:
            print(f"  ✗ Request failed: {e}"); failures.append(name)
        except Exception as e:
            print(f"  ✗ Read error: {e}"); failures.append(name)
    print("─"*20)
    return failures

async def phase_upload_parallel(session: aiohttp.ClientSession, base_url: str, root: str, files: List[str], token: Optional[str] = None,
                                label: str = "main", max_workers: int = 4):
    failures = []
    success_count = 0
    total_files = len(files)
    print(f"[{label}] Starting async upload of {total_files} patient bundle files with concurrency={max_workers}...")
    
    sem = asyncio.Semaphore(max_workers)
    tasks = [asyncio.create_task(upload_file_worker(session, sem, base_url, root, f, token)) for f in files]

    for i, future in enumerate(asyncio.as_completed(tasks)):
        filename, error_text = await future
        
        if error_text:
            failures.append((filename, error_text))
            print(f"  ✗ [{i+1}/{total_files}] Failed to upload {filename}.")
        else:
            success_count += 1
            print(f"  ✓ [{i+1}/{total_files}] Successfully uploaded {filename}")

    print(f"[{label}] Async upload phase complete. Success: {success_count}, Failures: {len(failures)}")
    return failures

async def main():
    # Use a slightly higher default for async, as it's more efficient
    default_workers = min(32, (os.cpu_count() or 1) * 5)
    ap = argparse.ArgumentParser(description="Upload Synthea FHIR bundles to HAPI in a safe order using asyncio.")
    default_base = (
        f"http://{os.getenv('LOCAL_ADDRESS', '0.0.0.0')}:"
        f"{os.getenv('MIDDLEMAN_PORT', '3000')}/"
        f"{os.getenv('FHIR_ROUTE', 'fhir_server')}/fhir"
    )
    ap.add_argument("--base-url", default=default_base)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--token", default=None)
    ap.add_argument("--retry", type=int, default=1)
    ap.add_argument("--workers", type=int, default=default_workers)
    args = ap.parse_args()


    print("\n\n\n" + "="*20 + f" base_url:{args.base_url} " + "="*20)


    if not os.path.isdir(args.dir): raise SystemExit(f"Directory not found: {args.dir}")

    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")

    connector = aiohttp.TCPConnector(limit=args.workers * 2, limit_per_host=args.workers)

    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(f"{args.base_url}/metadata", timeout=aiohttp.ClientTimeout(total=30)) as meta:
                if meta.status // 100 != 2:
                    print(f"Warning: GET /metadata returned {meta.status}")
        except aiohttp.ClientError as e:
            print(f"Warning: Could not GET /metadata: {e}")

        seeds, rest, skipped_meta = plan_files(args.dir)
        if skipped_meta:
            print(f"Skipping {len(skipped_meta)} Synthea metadata files (timestamp-prefixed): {skipped_meta}")

        seed_failures = await phase_upload_seeds(session, args.base_url, args.dir, seeds, token=args.token)
        if seed_failures:
            print("\nSome seed files failed; address those for references to resolve.")

        all_failures = await phase_upload_parallel(session, args.base_url, args.dir, rest, token=args.token,
                                                   label="main", max_workers=args.workers)

        for attempt in range(1, args.retry + 1):
            if not all_failures: break
            to_retry = [t for t in all_failures if looks_like_hapi_1091(t[1])]
            other = [t for t in all_failures if not looks_like_hapi_1091(t[1])]
            if not to_retry: all_failures = other; break
            files = [t[0] for t in to_retry]
            print(f"\nRetry pass {attempt}/{args.retry} for {len(files)} HAPI-1091 errors...")
            retry = await phase_upload_parallel(session, args.base_url, args.dir, files, token=args.token,
                                                label=f"retry {attempt}", max_workers=args.workers)
            all_failures = other + retry
            await asyncio.sleep(2)

    final_failure_files = sorted([f[0] for f in all_failures])
    print("\n" + "="*20 + " Summary " + "="*20)
    if seed_failures:
        print(f"Seed failures ({len(seed_failures)}): {seed_failures}")
    else:
        print("All seed files uploaded successfully.")
    if final_failure_files:
        print(f"Remaining failures after retries ({len(final_failure_files)}): {final_failure_files}")
        # TODO: figure out why about 3-6 of the synthea files fail to upload. formatting?
        print("Common cause: Unresolved inline match URLs.")
    else:
        print("All non-seed files uploaded successfully.")
    print("="*49)

if __name__ == "__main__":
    asyncio.run(main())
