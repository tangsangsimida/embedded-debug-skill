from __future__ import annotations

import pytest

from embedded_debug.gdb.controller import GDBController


class RecordingMI:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def command(self, command: str):
        self.commands.append(command)
        return type("Result", (), {"results": {"value": "42"}})()


@pytest.mark.parametrize("path", ["motor_speed", "controller.pid.kp", "app::motor_speed"])
def test_variable_read_accepts_simple_identifier_paths(path: str) -> None:
    mi = RecordingMI()
    assert GDBController(mi).variable_read(path) == "42"
    assert mi.commands == [f'-data-evaluate-expression "{path}"']


@pytest.mark.parametrize(
    "expression",
    ["motor_speed = 1", "motor_speed++", "set_speed(1)", "*motor_speed", "array[0]", "ptr->value"],
)
def test_variable_read_rejects_expressions_without_sending_to_gdb(expression: str) -> None:
    mi = RecordingMI()
    with pytest.raises(ValueError, match="--authorize-expression"):
        GDBController(mi).variable_read(expression)
    assert mi.commands == []


def test_explicit_arbitrary_expression_path_is_available_to_authorized_caller() -> None:
    mi = RecordingMI()
    assert GDBController(mi).variable_read("set_speed(1)", allow_arbitrary=True) == "42"
    assert mi.commands == ['-data-evaluate-expression "set_speed(1)"']
