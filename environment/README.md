
### How to run:

Setup with 

```bash
uv venv --python 3.10 --seed
source .venv/bin/activate
uv sync
```

and make sure the HAPI server is running.

```sh
# Cerebras
python eval.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1
```

```sh
# Groq
python eval.py \
  -m openai/gpt-oss-120b \
  -k GROQ_API_KEY \
  -b https://api.groq.com/openai/v1
```

```sh
# OpenRouter
python eval.py \
  -m openai/gpt-oss-120b \
  -k OPENROUTER_API_KEY \
  -b https://openrouter.ai/api/v1
```


