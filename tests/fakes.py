"""Small COM-like objects shared by model and expression tests."""


class FakeOperand:
    def __init__(self, name, *, array=False, parent=None, default=""):
        self.Name = name
        self.Prompt = name
        self.DefaultValue = default
        self.Required = False
        self.ControlType = 0
        self.Array = array
        self.Entry = False
        self.Exit = False
        self.ParentOperand = parent or ""


class FakeDefinition:
    def __init__(self, operands):
        self.Operands = operands


class FakeModule:
    def __init__(self):
        self.Definition = FakeDefinition(
            [
                FakeOperand("Name"),
                FakeOperand("Assignments", array=True),
                FakeOperand("Type", parent="Assignments"),
                FakeOperand("Value", parent="Assignments"),
            ]
        )
        self.values = {
            "Name": "Initialize",
            "Type(1)": "Variable",
            "Value(1)": "1",
            "Type(2)": "Attribute",
            "Value(2)": "2",
        }

    def Data(self, key):
        if key not in self.values:
            raise RuntimeError("missing tuple")
        return self.values[key]
