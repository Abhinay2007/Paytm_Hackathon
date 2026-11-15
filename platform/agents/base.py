"""Reusable structured-output base for Brain agents."""

import re
import time
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from platform import llm

OutputT = TypeVar("OutputT", bound=BaseModel)


def _is_retryable_llm_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if not isinstance(error, RuntimeError):
        return False

    # platform.llm currently maps transport errors to this message and HTTP
    # errors to a message containing their status code.
    message = str(error)
    if message == "Sarvam chat request failed":
        return True
    match = re.search(r"HTTP (\d{3})\)?\Z", message)
    return bool(match and (match.group(1) == "429" or match.group(1).startswith("5")))


class AgentBase(Generic[OutputT]):
    """Call the shared LLM interface and validate its reply as a Pydantic model.

    Subclasses provide their own prompt and output model. ``max_retries`` is
    the number of additional attempts after the initial call; invalid output
    and transient LLM failures both consume an attempt.
    """

    def __init__(
        self,
        system_prompt: str,
        output_model: type[OutputT],
        *,
        max_retries: int = 2,
        retry_delay: float = 0.0,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_delay < 0:
            raise ValueError("retry_delay must be non-negative")
        self.system_prompt = system_prompt
        self.output_model = output_model
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def run(self, user_prompt: str) -> OutputT:
        """Return validated structured output or raise after bounded attempts."""
        validation_feedback = ""
        for attempt in range(self.max_retries + 1):
            if attempt and self.retry_delay:
                time.sleep(self.retry_delay)
            request = user_prompt
            if validation_feedback:
                request = (
                    f"{user_prompt}\n\nThe previous response did not match the required "
                    f"schema. Correct these validation issues: {validation_feedback}"
                )

            try:
                raw_output = llm.chat(self.system_prompt, request)
            except (TimeoutError, RuntimeError) as exc:
                if not _is_retryable_llm_error(exc):
                    raise
                if attempt == self.max_retries:
                    raise
                continue

            try:
                return self.output_model.model_validate_json(raw_output)
            except ValidationError as exc:
                if attempt == self.max_retries:
                    raise
                validation_feedback = str(exc.errors(include_input=False))

        raise AssertionError("unreachable")
