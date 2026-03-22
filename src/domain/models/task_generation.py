"""
Task generation job model.
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from src.domain.models.task import Task


TaskGenerationStatus = Literal["queued", "running", "completed", "failed"]
TaskGenerationStepStatus = Literal["pending", "running", "completed", "failed"]


class TaskGenerationStep(BaseModel):
    """A single task generation step."""

    key: str
    label: str
    status: TaskGenerationStepStatus = "pending"
    message: str = ""


class TaskGenerationJob(BaseModel):
    """Task generation job."""

    job_id: str
    task_name: str
    status: TaskGenerationStatus = "queued"
    message: str = "Task queued, waiting to start."
    current_step: Optional[str] = None
    steps: List[TaskGenerationStep] = Field(default_factory=list)
    task: Optional[Task] = None
    error: Optional[str] = None
