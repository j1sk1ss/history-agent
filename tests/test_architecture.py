"""Keep transport and conversation primitives independent of orchestration."""

import ast
import unittest
from pathlib import Path

PACKAGE: Path = Path(__file__).resolve().parents[1] / "src/history_agent"


def imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(
                node.module + "." + alias.name for alias in node.names
            )
    return modules


class ArchitectureTest(unittest.TestCase):
    def test_source_size_and_function_limits(self) -> None:
        for path in PACKAGE.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                self.assertLessEqual(len(source.splitlines()), 300)
                for node in ast.walk(ast.parse(source)):
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ):
                        self.assertLessEqual(
                            node.end_lineno - node.lineno + 1,
                            100,
                            f"{path}:{node.lineno} {node.name}",
                        )

    def test_inference_adapters_do_not_import_application_layers(self) -> None:
        forbidden: tuple[str, ...] = tuple(
            "history_agent." + layer
            for layer in (
                "configuration",
                "history",
                "interfaces",
                "review",
                "runtime",
                "ui",
            )
        )
        for path in (PACKAGE / "inference").glob("*.py"):
            with self.subTest(module=path.name):
                self.assertFalse(
                    {
                        name
                        for name in imports(path)
                        if name.startswith(forbidden)
                    }
                )

    def test_model_loop_does_not_import_batch_orchestration(self) -> None:
        forbidden: tuple[str, ...] = tuple(
            "history_agent.review." + module
            for module in (
                "batch",
                "client",
                "investigation",
                "journal",
                "processor",
                "router",
            )
        )
        for module in (
            "agent",
            "completion",
            "execution",
            "messages",
            "parsing",
            "tools",
        ):
            path: Path = PACKAGE / "runtime" / (module + ".py")
            with self.subTest(module=module):
                self.assertFalse(
                    {
                        name
                        for name in imports(path)
                        if name.startswith(forbidden)
                    }
                )

    def test_internal_imports_are_absolute(self) -> None:
        for path in PACKAGE.rglob("*.py"):
            with self.subTest(module=str(path.relative_to(PACKAGE))):
                tree: ast.Module = ast.parse(path.read_text(encoding="utf-8"))
                self.assertFalse(
                    [
                        node.lineno
                        for node in ast.walk(tree)
                        if isinstance(node, ast.ImportFrom) and node.level
                    ]
                )
