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
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

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

    @model_validator(mode="before")
    @classmethod
    def _blank_means_omitted(cls, data: Any) -> Any:
        """An empty or whitespace-only string for an optional parameter means "not supplied".

        A flat `type: string` schema makes the small model write "" for "omit" (W4 round 1:
        `selector: ""`, `name: ""`). Only parameters that have a default are affected: a blank
        required parameter, and a blank unknown key, are still rejected.
        """
        if not isinstance(data, dict):
            return data
        optional = {n for n, f in cls.model_fields.items() if not f.is_required()}
        return {
            k: v
            for k, v in data.items()
            if not (k in optional and isinstance(v, str) and not v.strip())
        }


class GetPodsArgs(_Args):
    name: K8sName | None = Field(
        None, description="Exact pod name, no kind prefix. Omit to list all pods."
    )
    selector: Selector | None = Field(
        None, description="Label selector key=value, e.g. app=worker. Omit for no filtering."
    )


class GetPodLogsArgs(_Args):
    pod: K8sName = Field(
        description="Exact pod name as returned by get_pods, no kind prefix like 'pod/'."
    )
    container: K8sName | None = Field(
        None, description="Container name. Omit unless the pod has several containers."
    )
    previous: bool = Field(
        False, description="true for the logs of the previous (crashed) container."
    )
    tail_lines: int = Field(50, ge=1, le=200, description="Number of most recent log lines.")


class DescribePodArgs(_Args):
    pod: K8sName = Field(
        description="Exact pod name as returned by get_pods, no kind prefix like 'pod/'."
    )


class GetEventsArgs(_Args):
    involved_object: K8sName | None = Field(
        None,
        description="Bare name of one object, e.g. a pod name; no kind prefix like 'pod/'. "
        "Omit for all events.",
    )


class GetDeploymentArgs(_Args):
    name: K8sName | None = Field(
        None, description="Exact deployment name, no kind prefix. Omit to list all deployments."
    )


class GetConfigMapArgs(_Args):
    name: K8sName | None = Field(
        None, description="Exact ConfigMap name, no kind prefix. Omit to list all ConfigMaps."
    )


class GetServiceArgs(_Args):
    name: K8sName | None = Field(
        None, description="Exact Service name, no kind prefix. Omit to list all Services."
    )


class HttpProbeArgs(_Args):
    service: K8sName = Field(description="Exact Service name, no kind prefix.")
    port: int = Field(ge=1, le=65535, description="TCP port of the Service.")
    path: HttpPath = Field("/healthz", description="URL path to GET. Must start with '/'.")


class GetMetricsArgs(_Args):
    pass
