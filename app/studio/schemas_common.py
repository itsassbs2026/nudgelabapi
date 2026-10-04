"""Small request types shared by studio routes."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

TrainingIdStr = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")]
# The trainings a tester may start: 1 to 50 training ids.
TRAINING_IDS = Annotated[list[TrainingIdStr], Field(min_length=1, max_length=50)]
