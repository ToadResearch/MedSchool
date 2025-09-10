from dataclasses import dataclass
from typing import List, Optional
import json
from enum import Enum
from datasets import Dataset

class TaskType(Enum):
    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"

@dataclass
class Task:
    id: str
    type: List[TaskType] # TODO: might want more resolution on which operations are used on which Resouce IDs
    meta: dict | None  # TODO: this might hold info about the Resource types and IDs a task will use and CRUD type, if applicable
    input: dict
    output: dict

class TaskManager: 
    def __init__(self, task_filepath: Optional[str] = None, tasks_dataset: Optional[Dataset] = None):
        self.tasks: List[Task] = []
        if task_filepath:
            self.load_tasks(task_filepath)
        if tasks_dataset:
            self.load_tasks_from_dataset(tasks_dataset)

    def load_tasks(self, path: str):
        with open(path, "r") as f:
            data = json.load(f)
            for item in data:
                # read both lists and single strings in type field
                task_type = item["type"]
                if isinstance(task_type, str):
                    task_type = [TaskType(task_type)]
                else:
                    task_type = [TaskType(t) for t in task_type]
                task = Task(
                    id=item["id"], 
                    type=task_type,
                    meta=item.get("meta"), 
                    input=item["input"], 
                    output=item["output"]
                )
                self.tasks.append(task)

    def generate_task(self, config: dict):
        # TODO: generate a new task from scratch based on some configs
        task = Task(
            id="new_task", 
            type=[TaskType.CREATE],  # Default to single operation; can be extended via config
            meta={"description": "A new task"}, 
            input={"param": "value"}, 
            output={}
        )
        self.tasks.append(task)

    def clear_tasks(self):
        self.tasks = []

    def compatible_batches(self) -> List[List[Task]]:
        # TODO: based on the max_concurrent limit, split tasks into batches that can run concurrently
        # not sure if i want TaskManager to have knowledge of max_concurrent, but it is the TaskManager's job to figure out splits.
        # because it's unlikely that a new batch will be served to the SessionManager all at once -- more likely one at a time
        # so maybe pass in a list of task IDs to the next_task function and have it find a compatible task, hopefully this can
        # be computed quickly since it should be pretty heuristic -- if all read, good. if any non-read on a resource, don't share it, continue iterating over tasks.
        batches = []
        return batches

    def next_task(self) -> Optional[Task]:
        # TODO: maybe add logic to prevent certain tasks from happening at the same time 
        # e.g., tasks that use the same resources (delete this patient and update this patient at the same time)
        # need some way to restore resources to their original state after a non-read task is done
        if self.tasks:
            return self.tasks.pop(0)
        return None