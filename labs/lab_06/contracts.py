from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from labs.lab_06.errors import WorkflowStopError


class AgentInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str
    objective: str
    boundary: str

    def render(self) -> str:
        return (
            f"Role: {self.role.strip()}\n"
            f"Objective: {self.objective.strip()}\n"
            f"Boundary: {self.boundary.strip()}"
        )


class AgentContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_fields: list[str]
    output_fields: list[str]
    failure_statuses: list[str]
    trace_events: list[str]


@dataclass(frozen=True)
class ContractPayloadValidation:
    direction: str
    required_fields: list[str]
    provided_fields: list[str]
    missing_fields: list[str]


class ContractPayloadError(WorkflowStopError):
    def __init__(
        self,
        message: str,
        *,
        validation: ContractPayloadValidation,
    ) -> None:
        super().__init__(message, stop_reason="contract_validation_failed")
        self.validation = validation


def validate_instruction(instruction: AgentInstruction) -> AgentInstruction:
    if not isinstance(instruction, AgentInstruction):
        raise WorkflowStopError(
            "Agent instruction must define role, objective, and boundary fields.",
            stop_reason="agent_instruction_invalid",
        )
    fields = (instruction.role, instruction.objective, instruction.boundary)
    if any(not value.strip() or "TODO(lab_06)" in value for value in fields):
        raise WorkflowStopError(
            "Agent instruction must fill role, objective, and boundary fields.",
            stop_reason="agent_instruction_invalid",
        )
    return instruction


def validate_contract(contract: AgentContract) -> AgentContract:
    if not contract.input_fields or not contract.output_fields or not contract.failure_statuses or not contract.trace_events:
        raise WorkflowStopError(
            "Agent contract must define input, output, failure statuses, and trace events.",
            stop_reason="contract_definition_invalid",
        )
    return contract


def validate_contract_payload(
    contract: AgentContract,
    payload: Any,
    *,
    direction: str,
) -> ContractPayloadValidation:
    validate_contract(contract)
    if direction not in {"input", "output"}:
        raise WorkflowStopError(
            "Agent contract payload direction must be input or output.",
            stop_reason="contract_validation_failed",
        )

    required_fields = (
        contract.input_fields if direction == "input" else contract.output_fields
    )
    provided_fields = (
        sorted(str(field) for field in payload)
        if isinstance(payload, Mapping)
        else []
    )
    missing_fields = (
        [field for field in required_fields if field not in payload]
        if isinstance(payload, Mapping)
        else list(required_fields)
    )
    validation = ContractPayloadValidation(
        direction=direction,
        required_fields=list(required_fields),
        provided_fields=provided_fields,
        missing_fields=missing_fields,
    )
    if not isinstance(payload, Mapping):
        raise ContractPayloadError(
            f"Agent contract {direction} payload must be a mapping.",
            validation=validation,
        )
    if missing_fields:
        fields = ", ".join(missing_fields)
        raise ContractPayloadError(
            f"Agent contract {direction} payload is missing required fields: {fields}.",
            validation=validation,
        )
    return validation
