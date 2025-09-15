# eval.py
import argparse

from main import load_environment
from openai import OpenAI
from dotenv import load_dotenv
from datasets import Dataset

from src.config import get_settings

import os
import datetime


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run evals against an OpenAI-compatible endpoint."
    )
    p.add_argument(
        "-m",
        "--model",
        default="gpt-oss-120b",
        help="Model alias or provider model name.",
    )
    p.add_argument(
        "-k",
        "--api-key-var",
        default="CEREBRAS_API_KEY",
        help="Environment variable name that stores the API key.",
    )
    p.add_argument(
        "-b",
        "--base-url",
        default="https://api.cerebras.ai/v1",
        help="Base URL for the OpenAI-compatible API host.",
    )
    p.add_argument(
        "--task-filepath",
        help="Path to the task JSON to evaluate.",
    )
    # overrides task_filepath if set
    p.add_argument(
        "-t",
        "--task-filename",
        help="Task name to load from ./tasks/ directory (will append .json).",
    )
    p.add_argument(
        "--requested",
        type=int,
        default=1000,
        help="Max requested number of examples to evaluate.",
    )
    return p


def main():
    args = make_parser().parse_args()

    # Check that either task_filepath or task_filename is provided
    if not args.task_filepath and not args.task_filename:
        raise RuntimeError("Either --task-filepath or --task-filename must be provided.")

    # ---------- model client ----------
    load_dotenv()

    api_key = os.getenv(args.api_key_var)
    if not api_key:
        raise RuntimeError(
            f"Missing API key: environment variable '{args.api_key_var}' is not set."
        )

    client = OpenAI(
        base_url=args.base_url,
        api_key=api_key,
    )

    # ---------- env (make sure main.py sets tools = [] in load_environment) ----------
    if args.task_filename:
        task_filepath = os.path.join("tasks", f"{args.task_filename}.json")
    else:
        task_filepath = args.task_filepath
    

    env = load_environment(task_filepath=task_filepath)

    # ---------- choose num_examples safely ----------
    dataset_len = len(env.dataset) if getattr(env, "dataset", None) is not None else 0
    if dataset_len == 0:
        raise RuntimeError("Your dataset is empty. Check the task file path and contents.")
    requested = args.requested
    num_examples = min(requested, dataset_len)
    
    # Decide concurrency: up to sandbox cap, but never exceed num_examples
    sandbox_cap = get_settings().sandbox.max_concurrent_sessions or 1
    max_concurrent = max(1, min(sandbox_cap, num_examples))

    print("\n----- Evaluation Configuration -----")
    print(f"Running task: {task_filepath}")
    print(f"Using model: {args.model}")
    print(f"API endpoint: {args.base_url}")
    print(f"Dataset size: {dataset_len}")
    print(f"Evaluating {num_examples} examples (requested: {requested})")
    print(f"Max concurrent sessions: {max_concurrent}\n")

    # ---------- run eval ----------
    model = args.model
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
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model_dir = (
        model.replace("/", "_").replace(":", "_").replace(" ", "_")
    )  # keep filesystem happy
    output_dir = os.path.join("outputs", safe_model_dir, timestamp)
    os.makedirs(output_dir, exist_ok=True)

    # lines=False ensures a single list [ {...}, {...}, ... ]
    # force_ascii=False preserves unicode characters
    results_file = os.path.join(output_dir, "results.json")
    results.to_json(results_file, lines=False, force_ascii=False, indent=2)
    print(f"Results saved to {results_file}")


if __name__ == "__main__":
    main()
