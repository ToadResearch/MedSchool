#!/usr/bin/env python3
"""
- Seeds practitioners & hospitals first so inline match URLs resolve.
    - Needs to be done before patient bundles, as patient bundles
      use inline match URLs (e.g., Practitioner?identifier=...) which
      HAPI FHIR only resolves if those resources already exist, preventing HAPI-1091 errors.
    - Patient bundles need to have Practitioner and Hospital resources loaded first to reference!
- Then uploads all remaining bundles using asyncio for high performance.
- Retries files that failed with HAPI-1091 after seeding.
"""
import os, re, time, argparse, sys, asyncio
from typing import List, Tuple, Optional
import aiohttp

HAPI_MATCH_ERR = "Invalid match URL"

def read_bytes(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()

async def post_bundle(session: aiohttp.ClientSession, base_url: str, body: bytes, token: Optional[str] = None, timeout: int = 120) -> aiohttp.ClientResponse:
    headers = {"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"}
    if token: headers["Authorization"] = f"Bearer {token}"
    return await session.post(base_url, data=body, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout))

def is_seed_file(name: str) -> bool:
    return bool(re.search(r"^(practitionerInformation|hospitalInformation).+\.json$", name, re.IGNORECASE))

def plan_files(root: str):
    all_json = [f for f in os.listdir(root) if f.lower().endswith(".json")]
    seeds = sorted([f for f in all_json if is_seed_file(f)])
    rest = sorted([f for f in all_json if f not in seeds])
    return seeds, rest

def looks_like_hapi_1091(text: str) -> bool:
    return HAPI_MATCH_ERR in text or "HAPI-1091" in text

async def upload_file_worker(session: aiohttp.ClientSession, sem: asyncio.Semaphore, base_url: str, root_dir: str, filename: str, token: Optional[str]) -> Tuple[str, Optional[str]]:
    path = os.path.join(root_dir, filename)
    async with sem: # Acquire semaphore to limit concurrency
        try:
            body = read_bytes(path) # File I/O is still synchronous, but generally fast enough
            resp = await post_bundle(session, base_url, body, token=token)
            if 200 <= resp.status < 300:
                return filename, None
            return filename, await resp.text()
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
            body = read_bytes(path)
            resp = await post_bundle(session, base_url, body, token=token)
            if 200 <= resp.status < 300:
                print(f"  ✓ Success ({resp.status})")
            else:
                text = await resp.text()
                preview = text[:300].replace("\n", " ")
                print(f"  ✗ Failed ({resp.status}): {preview}")
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
    print(f"[{label}] Starting async upload of {total_files} patient bundle files...")
    
    sem = asyncio.Semaphore(max_workers)
    tasks = [upload_file_worker(session, sem, base_url, root, f, token) for f in files]
    
    for i, future in enumerate(asyncio.as_completed(tasks)):
        filename, error_text = await future
        
        if error_text:
            failures.append((filename, error_text))
            print(f"  ✗ [{i+1}/{total_files}] Failed to upload {filename}.")
        else:
            success_count += 1
            print(f"  ✓ [{i+1}/{total_files}] Successfully uploaded {filename}.")

    print(f"[{label}] Async upload phase complete. Success: {success_count}, Failures: {len(failures)}")
    return failures

async def main():
    # Use a slightly higher default for async, as it's more efficient
    default_workers = min(32, (os.cpu_count() or 1) * 5)
    ap = argparse.ArgumentParser(description="Upload Synthea FHIR bundles to HAPI in a safe order using asyncio.")
    ap.add_argument("--base-url", default="http://localhost:8080/fhir")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--token", default=None)
    ap.add_argument("--retry", type=int, default=1)
    ap.add_argument("--workers", type=int, default=default_workers)
    args = ap.parse_args()

    if not os.path.isdir(args.dir): raise SystemExit(f"Directory not found: {args.dir}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{args.base_url}/metadata", timeout=aiohttp.ClientTimeout(total=30)) as meta:
                if meta.status // 100 != 2:
                    print(f"Warning: GET /metadata returned {meta.status}")
        except aiohttp.ClientError as e:
            print(f"Warning: Could not GET /metadata: {e}")

        seeds, rest = plan_files(args.dir)
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
        print("✅ All seed files uploaded successfully.")
    if final_failure_files:
        print(f"Remaining failures after retries ({len(final_failure_files)}): {final_failure_files}")
        # TODO: figure out why about 6 of the synthea files fail to upload. formatting?
        print("Common cause: Unresolved inline match URLs.")
    else:
        print("✅ All non-seed files uploaded successfully.")
    print("="*49)

if __name__ == "__main__":
    asyncio.run(main())