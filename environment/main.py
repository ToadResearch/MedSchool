# environment/main.py
from __future__ import annotations

import verifiers as vf
from datasets import Dataset

from medschoolenv import MedSchoolEnv
from src.rubrics import ...

def load_environment(**kwargs):
    """
    Verifiers entrypoint. Returns a configured MedSchoolEnv.

    Optional kwargs (forwarded / handled here):
      - system_prompt: str
      - system_prompt_path: str (default './configs/system-prompt.txt')
      - dataset_path: str (default './example/data.json')
      - max_turns: int (default 10)
      - container_limit: int (default comes from configs/sandbox.yaml)
      - task_filepath: str (path to a JSON list of tasks; e.g., 'environment/tasks/toy_tasks.json')

    Any extra kwargs are passed through to MultiTurnEnv (verifiers).
    """


    parser = vf.PlainParser()
    rubric = vf.Rubric(funcs=[parser.get_format_reward_func()], weights=[0.1])


    prompt_template = """{input}\n{context}"""

    # ---- system prompt ----

    system_prompt_path = kwargs.get("system_prompt_path", "./configs/system-prompt.txt")

    try:

        with open(system_prompt_path, 'r') as f:

            default_system_prompt = f.read().strip()

    except FileNotFoundError:

        default_system_prompt = (

            "You can use available tools (FHIR, OpenFDA, Terminal) to fetch and summarize clinical data. "

            "Prefer tool calls over guesses. Be concise and surface key fields (ids, names, dates, codes, values)."

        )

    system_prompt = kwargs.get("system_prompt", default_system_prompt)

    # ---- dataset ----

    dataset_path = kwargs.get("dataset_path", "./example/data.json")

    try:

        dataset = Dataset.from_json(dataset_path)

    except Exception as e:

        # fallback to default

        dataset = vf.Dataset.from_list(

            [

                {

                    "prompt": [

                        {"role": "user", "content": "Hi! Insert task here for all tasks."}

                    ]

                }

            ]

        )

    # ---- construct environment ----
    env = MedSchoolEnv(
        dataset=dataset,
        parser=parser,
        rubric=rubric,
        system_prompt=system_prompt,
        max_turns=int(kwargs.get("max_turns", 10)),
        container_limit=kwargs.get("container_limit"),  # falls back to configs/sandbox.yaml if None
        # Any other unknown kwargs go to MultiTurnEnv (safe to ignore or extend later)
        **{k: v for k, v in kwargs.items() if k not in {"system_prompt", "system_prompt_path", "dataset_path", "max_turns", "container_limit", "task_filepath"}}
    )

    # ---- optional: load a task file into the env’s TaskManager ----
    task_filepath = kwargs.get("task_filepath")
    if task_filepath:
        try:
            env.session_manager.task_manager.load_tasks(task_filepath)
        except Exception as e:
            # Keep env usable even if tasks fail to load; caller can decide what to do.
            env.logger.warning(f"Failed to load tasks from {task_filepath}: {e}")

    return env
