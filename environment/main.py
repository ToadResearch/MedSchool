# environment/main.py
from __future__ import annotations

import unicodedata
import verifiers as vf
from datasets import Dataset
from medschoolenv import MedSchoolEnv
from datasets.utils.logging import disable_progress_bar
disable_progress_bar() # so map doesn't print progress bars


def to_vf_format(example: dict, system_prompt: str) -> dict:
    # create a prompt column with system prompt and initial task questions
    # build `prompt` (chat messages) and `answer` columns so verifiers doesn't look for "question"
    task = example.get("input", {}).get("task", "")
    ctx  = example.get("input", {}).get("context", "")
    if ctx:
        user_text = f"{task}\nContext: {ctx}"
    else:
        user_text = task

    # Chat-style prompt the library expects if "prompt" already present
    prompt = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    # Ensure answer is a string for comparison
    ans = example.get("output", {}).get("answer", "")
    ans = str(ans)

    return {
        "id": example.get("id", ""),
        "prompt": prompt,
        "answer": ans,
    }


def load_environment(
        system_prompt_path: str = "./configs/system-prompt.txt",
        task_filepath: str = "./tasks/toy_tasks.json",
        max_turns: int = 10,
        container_limit: int | None = None,
        **kwargs
        ):
    
    # ---- system prompt ----
    try:
        with open(system_prompt_path, 'r') as f:
            system_prompt = f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f"System prompt file not found at {system_prompt_path}. Please provide a valid path.")

    # ---- dataset ----
    try:
        tasks = Dataset.from_json(task_filepath)
    except FileNotFoundError:
        raise FileNotFoundError(f"Task file not found at {task_filepath}. Please provide a valid path.")
    
    # create a prompt column with system prompt and initial task questions
    dataset = tasks.map(to_vf_format, fn_kwargs={"system_prompt": system_prompt})

    # ---- tools ----
    tools=[] # extra tools to add to the internal tool registry

    # ---- parser ----
    parser = vf.Parser() # just basic parser for now

    # ---- rubric ----
    def correctness(parser, completion, answer):
        # reward if final response contains the answer
        response = parser.parse_answer(completion) or ''
        # Normalize nonstandard spaces and apostrophes
        response = unicodedata.normalize('NFKC', response)
        answer = unicodedata.normalize('NFKC', answer)
        return 1.0 if answer.lower().strip() in response.lower().strip() else 0.0

    rubric = vf.Rubric(funcs=[correctness], weights=[1.0])
    
    return MedSchoolEnv(
        dataset=dataset,
        tools=tools,
        parser=parser,
        rubric=rubric,
        system_prompt=system_prompt,
        max_turns=max_turns,
        container_limit=container_limit,  # falls back to configs/sandbox.yaml if None
        **kwargs
    )

if __name__ == "__main__":
    env = load_environment()