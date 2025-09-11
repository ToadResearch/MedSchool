# eval.py
from main import load_environment
from openai import OpenAI
from dotenv import load_dotenv
from verifiers.utils.message_utils import sanitize_tool_calls
from datasets import Dataset

from src.config import get_settings  # NEW: read sandbox cap

import os
import json
import pickle
import datetime

# ---------- model client ----------
load_dotenv()
client = OpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=os.getenv("CEREBRAS_API_KEY"),
)

# ---------- env (make sure main.py sets tools = [] in load_environment) ----------
task_filepath = "./tasks/terminology.json"
# task_filepath = "./tasks/openfda.json"
# task_filepath = "./tasks/counts.json"
# task_filepath = "./tasks/toy_tasks.json"
# task_filepath = "./tasks/single.json"
env = load_environment(task_filepath=task_filepath)

# ---------- choose num_examples safely ----------
dataset_len = len(env.dataset) if getattr(env, "dataset", None) is not None else 0
if dataset_len == 0:
    raise RuntimeError("Your dataset is empty. Check the task file path and contents.")
requested = 1000
num_examples = min(requested, dataset_len)

# Decide concurrency: up to sandbox cap, but never exceed num_examples
sandbox_cap = get_settings().sandbox.max_concurrent_sessions or 1
max_concurrent = max(1, min(sandbox_cap, num_examples))


# ---------- run eval ----------
model = "gpt-oss-120b"
results = env.evaluate(
    client=client,
    model=model,
    num_examples=num_examples,
    rollouts_per_example=1,
    max_concurrent=max_concurrent,
)

results: Dataset = env.make_dataset(
    results=results,
    push_to_hub=False,
)

# remove the "info" field
for col in results.column_names:
    if col.endswith("info"):
        results = results.remove_columns(col)

# ---------- save as a single JSON array ----------
output_dir = f"outputs/{model}/{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
os.makedirs(output_dir, exist_ok=True)

# lines=False ensures a single list [ {...}, {...}, ... ]
# force_ascii=False preserves unicode characters
results.to_json(f"{output_dir}/results.json", lines=False, force_ascii=False)
print(f"Results saved to {output_dir}/results.json")