"""Predictor-side Experiment 002 modules must not touch the evaluation data."""

import ast
import builtins
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.e002_support import article, build_db

PACKAGE = Path(__file__).resolve().parents[1] / "research" / "e002"
SCORER_MODULES = {"score"}
FORBIDDEN_WORDS = ("gold", "score", "evaluation")


def predictor_files():
    return sorted(p for p in PACKAGE.glob("*.py") if p.stem not in SCORER_MODULES)


def code_strings_and_names(tree):
    """Names, attributes, imports and non-docstring string constants."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) \
                    and isinstance(node.body[0].value, ast.Constant):
                docstrings.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.arg)):
            yield getattr(node, "name", None) or getattr(node, "arg", "")
        elif isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings:
            yield node.value


class StaticLeakageTests(unittest.TestCase):
    def test_predictor_modules_exist(self):
        names = {p.stem for p in predictor_files()}
        self.assertTrue({"__init__", "candidates", "corpus", "predict", "rules", "stage_b",
                         "text"} <= names)

    def test_no_reference_to_gold_scorer_or_evaluation(self):
        for path in predictor_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for item in code_strings_and_names(tree):
                for word in FORBIDDEN_WORDS:
                    with self.subTest(file=path.name, item=item):
                        self.assertNotIn(word, item.casefold())

    def test_only_allowed_imports(self):
        allowed_local = {"rules", "text", "corpus", "candidates", "predict"}
        for path in predictor_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level:
                    for alias in node.names:
                        target = node.module or alias.name
                        with self.subTest(file=path.name, target=target):
                            self.assertIn(target, allowed_local)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = node.module if isinstance(node, ast.ImportFrom) else None
                    for name in [module] if module else [a.name for a in node.names]:
                        with self.subTest(file=path.name, module=name):
                            self.assertFalse(name.startswith(("research", "atlas_signal")))


class RuntimeLeakageTests(unittest.TestCase):
    def test_loading_opens_only_the_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = build_db(Path(tmp) / "c.sqlite3", [article(1), article(2)])
            saved = {m: sys.modules.pop(m) for m in list(sys.modules)
                     if m.startswith("research.e002")}
            self.addCleanup(sys.modules.update, saved)
            opened = []
            real_open = builtins.open

            def tracking_open(file, *args, **kwargs):
                opened.append(str(file))
                return real_open(file, *args, **kwargs)

            with mock.patch.object(builtins, "open", tracking_open), \
                    mock.patch.object(io, "open", tracking_open):
                corpus = importlib.import_module("research.e002.corpus")
                importlib.import_module("research.e002.text")
                corpus.load_corpus(path, expected_sha256=corpus.file_sha256(path))
            self.assertTrue(opened)
            self.assertEqual({Path(p).resolve() for p in opened}, {path.resolve()})
            self.assertNotIn("research.e002.score", sys.modules)


class StageALeakageTests(unittest.TestCase):
    def test_stage_a_runs_without_gold_and_opens_only_corpus_and_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            db = build_db(tmp / "c.sqlite3", [article(1, title="Acme plant", summary="A £7m plan."),
                                              article(2, title="Acme plant", summary="A £7m plan.")])
            out = tmp / "out" / "a.json"
            saved = {m: sys.modules.pop(m) for m in list(sys.modules)
                     if m.startswith("research.e002")}
            self.addCleanup(sys.modules.update, saved)
            opened = []
            real_open = builtins.open

            def guarded_open(file, *args, **kwargs):
                opened.append(str(file))
                if "gold" in str(file).casefold():
                    raise FileNotFoundError(file)
                return real_open(file, *args, **kwargs)

            before = set(sys.modules)
            cwd = os.getcwd()
            os.chdir(tmp)  # no repository files are reachable by relative path
            try:
                with mock.patch.object(builtins, "open", guarded_open),                         mock.patch.object(io, "open", guarded_open),                         contextlib.redirect_stdout(io.StringIO()):
                    predict = importlib.import_module("research.e002.predict")
                    corpus = importlib.import_module("research.e002.corpus")
                    code = predict.main(["--db", str(db), "--out", str(out),
                                         "--expected-sha256", corpus.file_sha256(db)])
            finally:
                os.chdir(cwd)
            self.assertEqual(code, 0)
            self.assertTrue(out.exists())
            self.assertEqual({Path(p).resolve() for p in opened}, {db.resolve(), out.resolve()})
            imported = set(sys.modules) - before
            self.assertIn("research.e002.predict", imported)
            self.assertFalse([m for m in imported if "score" in m or "gold" in m])


class StageBLeakageTests(unittest.TestCase):
    def test_stage_b_loader_opens_only_corpus_and_candidate_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            rows = [article(i, url=f"https://example.test/n/{i}", title=f"Acme plant {i}",
                            summary="A \u00a37m plan.") for i in (1, 2)]
            db = build_db(tmp / "c.sqlite3", rows)
            saved = {m: sys.modules.pop(m) for m in list(sys.modules)
                     if m.startswith("research.e002")}
            self.addCleanup(sys.modules.update, saved)
            before = set(sys.modules)
            opened = []
            real_open = builtins.open

            def guarded_open(file, *args, **kwargs):
                opened.append(str(file))
                if "gold" in str(file).casefold():
                    raise FileNotFoundError(file)
                return real_open(file, *args, **kwargs)

            corpus_mod = importlib.import_module("research.e002.corpus")
            predict_mod = importlib.import_module("research.e002.predict")
            c = corpus_mod.load_corpus(db, expected_sha256=corpus_mod.file_sha256(db))
            artifact_path = tmp / "candidates.json"
            artifact_path.write_text(predict_mod.canonical_json(predict_mod.stage_a_artifact(c)),
                                     encoding="utf-8")
            sha = corpus_mod.file_sha256(artifact_path)
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch.object(builtins, "open", guarded_open), \
                        mock.patch.object(io, "open", guarded_open):
                    stage_b = importlib.import_module("research.e002.stage_b")
                    candidates_mod = importlib.import_module("research.e002.candidates")
                    c = corpus_mod.load_corpus(db, expected_sha256=corpus_mod.file_sha256(db))
                    model = candidates_mod.corpus_model(c.articles)
                    stage_b.load_candidate_artifact(artifact_path, sha, c, model)
            finally:
                os.chdir(cwd)
            self.assertEqual({Path(p).resolve() for p in opened},
                             {db.resolve(), artifact_path.resolve()})
            imported = set(sys.modules) - before
            self.assertIn("research.e002.stage_b", imported)
            self.assertFalse([m for m in imported if "score" in m or "gold" in m])


REAL_GOLD_NAME = "event-relationship" + "-gold.json"
SEALED_ARTIFACT_NAME = "e002-stage-a-run1" + "-candidates.json"


class ScorerArchitectureTests(unittest.TestCase):
    def test_no_predictor_module_imports_the_scorer(self):
        for path in predictor_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [a.name for a in node.names]
                elif isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                else:
                    continue
                with self.subTest(file=path.name):
                    self.assertFalse([n for n in names if n.split(".")[-1] == "score"])

    def test_scorer_does_not_regenerate_candidates_or_read_the_corpus(self):
        tree = ast.parse((PACKAGE / "score.py").read_text(encoding="utf-8"))
        used = set(code_strings_and_names(tree))
        forbidden = {"generate_candidates", "build_features", "pair_signals", "load_corpus",
                     "connect_readonly", "sqlite3", "corpus.py"}
        self.assertFalse(used & forbidden)
        self.assertFalse([u for u in used if "docs/research" in u or REAL_GOLD_NAME in u])

    def test_scorer_opens_only_the_named_synthetic_files(self):
        from tests.test_e002_score import LEX, artifact, gold, pair
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cand_path, gold_path, out = tmp / "c.json", tmp / "g.json", tmp / "s.json"
            cand_path.write_text(json.dumps(artifact({(1, 2): LEX})), encoding="utf-8")
            gold_path.write_text(json.dumps(gold([("P2-01", 2, [pair(1, 2, "same_event")])])),
                                 encoding="utf-8")
            opened = []
            real_open = builtins.open

            def guarded_open(file, *args, **kwargs):
                name = Path(str(file)).name
                if name in (REAL_GOLD_NAME, SEALED_ARTIFACT_NAME):
                    raise AssertionError(f"scorer opened {name}")
                opened.append(str(file))
                return real_open(file, *args, **kwargs)

            from research.e002 import score
            with mock.patch.object(builtins, "open", guarded_open),                     mock.patch.object(io, "open", guarded_open),                     contextlib.redirect_stdout(io.StringIO()):
                code = score.main(["stage-a", "--candidates", str(cand_path), "--gold",
                                   str(gold_path), "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual({Path(p).resolve() for p in opened},
                             {cand_path.resolve(), gold_path.resolve(), out.resolve()})

    def test_no_test_uses_the_sealed_real_artifact(self):
        tests_dir = Path(__file__).resolve().parent
        for path in sorted(tests_dir.glob("test_e002_*.py")):
            with self.subTest(file=path.name):
                self.assertNotIn(SEALED_ARTIFACT_NAME, path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
