The env is almost there!


### Basic repl demo for now

Setup with 

```bash
uv venv --python 3.10 --seed
source .venv/bin/activate
uv sync
```

and make sure the HAPI server is running. Then you can run the repl files inside `repls/` repo:

To test a few FHIR retrieval queries:
```bash
python repls/fhir_repl.py
```

To test function calls:
```bash
python repls/test_repl.py
```
