Setup with 

```bash
uv venv --python 3.10 --seed
source .venv/bin/activate
uv sync
```

and make sure the HAPI server is running. Then run

```bash
python repl.py
```
You’ll see a menu like:

```
Controls
──────────────────────────────────────────────────────────────────────
  1   Random resource across ALL types
  2   List counts for each resource type
  3   Random resource BY TYPE
  4   Read a specific resource (type + id or identifier)
  5   Show CapabilityStatement basics
  t   List available resource types
  r   Refresh resource types from /metadata
  m   Show this menu again
  c   Clear screen
  q   Quit
```

**Tips**

* Press `?` at the **type** prompt to list all resource types.
* Option **4** accepts:

  * **server id** (e.g., `1189`) → `GET /Patient/1189`
  * **identifier value** (e.g., `0e5401fd-b241-...`) → `GET /Patient?identifier=value`
  * **system|value** (e.g., `http://hospital.smarthealthit.org|0e54...`) → `GET /Patient?identifier=system|value`
* If you mistype a type, you’ll get closest matches and can retry.
* `c` clears the screen **and** reprints the menu.



* Loads `FHIR_BASE_URL` from `.env` using `python-dotenv` (see **Config**). If missing, it can derive from `FHIR_PROXY_PUBLIC_BASE`.
* Async FHIR client (`httpx`) with helpers:

  * `get_capability()` → CapabilityStatement JSON
  * `read(resource_type, id)` → `GET /{type}/{id}`
  * `search(resource_type, params)` → `GET /{type}?…`
  * `count(resource_type)` → `_summary=count` with `_total=accurate`
  * `sample(resource_type)` → random document using `_count=0` + `_summary=count` + random `_offset`
  * `search_by_identifier(resource_type, value, system=None)` → `identifier=value` or `system|value`
  * `read_by_identifier(resource_type, value, system=None)` → convenience wrapper returning the first match
* A `ToolEnv` (if you use Verifiers) that exposes read-only tools.
* A friendly **REPL** for manual testing (colors, menus, type‑picker, identifier support).

> **Scope**: Read-only. No writes, deletes, or updates.

---

## 🧪 Programmatic examples

### Standalone (just the client)

```python
import asyncio
from environment.src.config import AppConfig
from environment.src.fhir_client import AsyncFHIRClient

async def main():
    cfg = AppConfig.load(timeout_s=30)
    async with AsyncFHIRClient(base_url=cfg.fhir_base_url, timeout_s=cfg.timeout_s) as client:
        caps = await client.get_capability()
        print("FHIR version:", caps.get("fhirVersion"))

        # Count Patients
        n = await client.count("Patient")
        print("Patients:", n)

        # Read by server id
        p = await client.read("Patient", "1189")
        print("Patient 1189 name:", p.get("name", [{}])[0])

        # Read by identifier (value only)
        p2 = await client.read_by_identifier("Patient", value="0e5401fd-b241-…")
        print("By identifier:", p2 and p2.get("id"))

asyncio.run(main())
```

### With Verifiers (ToolEnv)

```python
import verifiers as vf
from environment.main import load_environment

# Create a read-only ToolEnv against your FHIR instance
env = load_environment()

# Evaluate a minimal dataset (or your own tasks)
client = vf.get_openai_client()
results = env.evaluate(client, "gpt-4o-mini", num_examples=3, rollouts_per_example=1)
print(results)
```

> `load_environment(**kwargs)` passes through extra Verifiers options (e.g., sampler sizes, parser config).

---