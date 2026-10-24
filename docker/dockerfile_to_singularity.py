#!/usr/bin/env python3
"""Convert the SVN-ICP Dockerfile with spython, preserving its build environment."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import tempfile

try:
    from spython.main.parse.parsers import DockerParser
    from spython.main.parse.writers import SingularityWriter
except ImportError as error:
    raise SystemExit("Install spython: python3 -m pip install -r docker/requirements-singularity.txt") from error


VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def unquote(value):
    if value[:1] in {"'", '"'}:
        words = shlex.split(value)
        if len(words) != 1:
            raise ValueError(f"Expected one quoted Docker value: {value}")
        return words[0]
    return value


class EnvironmentValue:
    """Keep literal text separate from references to inherited image variables."""

    def __init__(self, parts):
        self.parts = parts

    @classmethod
    def literal(cls, value):
        return cls([(False, value)])

    @classmethod
    def expand(cls, value, variables):
        parts = []
        position = 0
        for match in VARIABLE.finditer(value):
            parts.append((False, value[position:match.start()]))
            name = match[1] or match[2]
            parts.extend(variables[name].parts if name in variables else [(True, name)])
            position = match.end()
        parts.append((False, value[position:]))
        return cls(parts)

    def shell(self):
        return "".join('"${' + value + '}"' if reference else shlex.quote(value)
                       for reference, value in self.parts if reference or value) or "''"


class BuildEnvironmentParser(DockerParser):
    """Adapt spython's ARG, ENV, SHELL and RUN handling for this Dockerfile."""

    def __init__(self, filename, build_args):
        self.build_args = build_args
        self.declared_args = set()
        self.variables = {}
        self.command_shell = ["/bin/sh", "-c"]
        super().__init__(str(filename), load=False)
        logical = []
        pending = ""
        for line in self.lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith("\\"):
                pending += line[:-1] + " "
            else:
                logical.append(pending + line)
                pending = ""
        if pending:
            raise ValueError("Unfinished Dockerfile continuation")
        if sum(bool(re.match(r"FROM\s", line, re.IGNORECASE)) for line in logical) != 1:
            raise ValueError("This converter requires a single-stage Dockerfile")
        self.lines = logical
        self.parse()
        if len(self.recipe) != 1:
            raise ValueError("This converter supports the repository's single-stage Dockerfile")
        unknown = build_args.keys() - self.declared_args
        if unknown:
            raise ValueError(f"Unknown build arguments: {', '.join(sorted(unknown))}")

    def _get_mapping(self, line, parser=None, previous=None):
        if line.split(None, 1)[0].upper() == "SHELL":
            return self._shell
        return super()._get_mapping(line, parser, previous)

    def _shell(self, line):
        shell = json.loads(line.split(None, 1)[1])
        if not isinstance(shell, list) or not shell or not all(isinstance(item, str) for item in shell):
            raise ValueError("SHELL must be a nonempty JSON array of strings")
        self.command_shell = shell

    def _arg(self, line):
        name, separator, default = line.split(None, 1)[1].partition("=")
        if not NAME.fullmatch(name):
            raise ValueError(f"Invalid build argument: {name}")
        self.declared_args.add(name)
        if name not in self.build_args and not separator:
            raise ValueError(f"Supply --build-arg {name}=VALUE")
        value = self.build_args[name] if name in self.build_args else unquote(default)
        self.args[name] = value
        self.variables[name] = EnvironmentValue.literal(value)
        self.recipe[self.active_layer].install.append("export " + name + "=" + shlex.quote(value))

    def _env(self, line):
        # Resolve each instruction against preceding ARG/ENV values, as Docker does.
        updates = {}
        for assignment in self.parse_env(self._setup("ENV", line)):
            name, value = assignment.split("=", 1)
            if not NAME.fullmatch(name):
                raise ValueError(f"Invalid environment variable: {name}")
            updates[name] = EnvironmentValue.expand(unquote(value), self.variables)
        for name, value in updates.items():
            assignment = name + "=" + value.shell()
            self.recipe[self.active_layer].install.append("export " + assignment)
            self.recipe[self.active_layer].environ.append(assignment)
        self.variables.update(updates)

    def _run(self, line):
        command = line.split(None, 1)[1]
        if command.startswith("["):
            try:
                arguments = json.loads(command)
            except json.JSONDecodeError:
                # Shell commands such as [[ ... ]] are not JSON exec form.
                arguments = [*self.command_shell, command]
            else:
                if (not isinstance(arguments, list) or not arguments or
                        not all(isinstance(item, str) for item in arguments)):
                    raise ValueError("Exec-form RUN must be a nonempty JSON array of strings")
        else:
            arguments = [*self.command_shell, command]
        # Separate processes preserve Docker RUN boundaries and error propagation.
        self.recipe[self.active_layer].install.append(shlex.join(arguments))


def convert(dockerfile, build_args):
    parser = BuildEnvironmentParser(dockerfile, build_args)
    definition = SingularityWriter(parser.recipe).convert()
    return re.sub(r"(?m)^%post$", "%post -c /bin/bash\nset -e", definition) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dockerfile", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--build-arg", action="append", default=[])
    args = parser.parse_args()
    try:
        build_args = {}
        for argument in args.build_arg:
            name, separator, value = argument.partition("=")
            if not separator or not NAME.fullmatch(name):
                raise ValueError("--build-arg requires NAME=VALUE")
            build_args[name] = value
        definition = convert(args.dockerfile, build_args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(definition)
                stream.close()
                os.link(temporary, args.output)
            finally:
                temporary.unlink()
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
