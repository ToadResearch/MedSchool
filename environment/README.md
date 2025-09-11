
### How to run:

First, set up the environment:

```bash
uv venv --python 3.10 --seed
source .venv/bin/activate
uv sync
```

Make sure the HAPI server is running.


```bash
# Cerebras
python eval.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1 \
  -t single
```

```bash
# Groq
python eval.py \
  -m openai/gpt-oss-120b \
  -k GROQ_API_KEY \
  -b https://api.groq.com/openai/v1
  -t basic
```
```bash
# OpenRouter
python eval.py \
  -m openai/gpt-oss-120b \
  -k OPENROUTER_API_KEY \
  -b https://openrouter.ai/api/v1
  -t basic
```



**CLI Args**

The `eval.py` script supports various options:
- `-m, --model`: Model alias or provider model name (default: gpt-oss-120b)
- `-k, --api-key-var`: Environment variable for API key (default: CEREBRAS_API_KEY)
- `-b, --base-url`: Base URL for the API (default: https://api.cerebras.ai/v1)
- `-t, --task-filename`: Task name to load from ./tasks/ directory (appends .json automatically)
- `--task-filepath`: Full path to the task JSON file
- `--requested`: Max number of examples to evaluate (default: 1000)

**Note**: Either `--task-filepath` or `--task-filename` must be provided.

New tasks can be added as `<task_name>.json` to the [tasks directory](environment/tasks), and my be called using the flag `-t <task_name>`.