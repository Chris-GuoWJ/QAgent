"""Hardware-neutral experiment port. An absent lookup must be authoritative."""

from typing import Protocol

from ..schemas import Capabilities, ExperimentRequest, JobStatus, LookupResult, Measurement


class BackendError(Exception):
    """A read/query failed; submission ambiguity uses its own exception."""


class SubmissionUnknown(BackendError):
    """The backend might have accepted the request. Never blindly resubmit."""


class SubmissionRejected(BackendError):
    """The backend guarantees that this submission was NOT accepted."""


class ExperimentBackend(Protocol):
    @property
    def identity(self) -> str: ...

    def capabilities(self) -> Capabilities: ...

    def submit(self, request: ExperimentRequest) -> str: ...

    def lookup(self, request_id: str) -> LookupResult: ...

    def status(self, job_id: str) -> JobStatus: ...

    def fetch(self, job_id: str) -> Measurement: ...
