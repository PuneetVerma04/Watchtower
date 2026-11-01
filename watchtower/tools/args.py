"""Argument models for the tools.

The model supplies these arguments, and a compromised log can influence what the model asks
for, so treat every value as hostile. These models also become the JSON schema handed to the
LLM (registry.py).

Rules:
- names match DNS-1123 and are length-bounded
- nothing starting with '-' (would become a kubectl flag)
- no namespace parameter at all, and unknown keys are rejected (extra="forbid"), so a model
  that invents `namespace=kube-system` gets a validation error, not a silent pass-through
- every integer bounded
"""

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$")

# key=value pairs, comma separated. A key must start alphanumeric, so '-o=yaml' cannot match.
_PAIR = r"[A-Za-z0-9][A-Za-z0-9._/-]*=[A-Za-z0-9._-]*"
SELECTOR_RE = re.compile(rf"^{_PAIR}(,{_PAIR}){{0,9}}$")

HTTP_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]{0,100}$")


def _no_dot_dot(value: str) -> str:
    if ".." in value:
        raise ValueError("'..' is not allowed")
    return value


K8sName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=253, pattern=K8S_NAME_RE.pattern),
    AfterValidator(_no_dot_dot),
]

Selector = Annotated[
    str,
    StringConstraints(min_length=3, max_length=300, pattern=SELECTOR_RE.pattern),
]

HttpPath = Annotated[
    str,
    StringConstraints(max_length=101, pattern=HTTP_PATH_RE.pattern),
    AfterValidator(_no_dot_dot),
]


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetPodsArgs(_Args):
    name: K8sName | None = Field(None, description="Only this pod")
    selector: Selector | None = Field(None, description="Label selector, e.g. app=worker")


class GetPodLogsArgs(_Args):
    pod: K8sName
    container: K8sName | None = Field(None, description="Only needed for multi-container pods")
    previous: bool = Field(False, description="Logs of the previous (crashed) container")
    tail_lines: int = Field(50, ge=1, le=200, description="Number of most recent lines")


class DescribePodArgs(_Args):
    pod: K8sName


class GetEventsArgs(_Args):
    involved_object: K8sName | None = Field(None, description="Only events about this object")


class GetDeploymentArgs(_Args):
    name: K8sName | None = None


class GetConfigMapArgs(_Args):
    name: K8sName | None = None


class GetServiceArgs(_Args):
    name: K8sName | None = None


class HttpProbeArgs(_Args):
    service: K8sName
    port: int = Field(ge=1, le=65535)
    path: HttpPath = Field("/healthz", description="Path to GET, e.g. /healthz")


class GetMetricsArgs(_Args):
    pass
