"""The tree matches the plan: folders, node identities (section 1.3), ports (4.1), READMEs, no stubs, frozen parts.

Only what git tracks is required here, so a fresh clone passes. Checks that need the models on disk skip, with a clear
message, when the models aren't installed.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODES = ("incubator-1", "incubator-2", "hvac", "animal-protection")
FOLDER_READMES = (  # every planned folder that holds tracked files, by its README
    "README.md", "launcher/README.md", "third_party/README.md", "third_party/llama.cpp/README.md",
    "third_party/nats-server/README.md", "models/README.md", "matrix/README.md", "nodes/README.md",
    "interface/README.md", "interface/web/README.md", "interface/gateway/README.md", "interface/contracts/README.md",
    "tests/README.md", "tests/llm/README.md", "tests/llm/golden/README.md", "tests/llm/retrieval/README.md",
    "tests/llm/integration/README.md", "tests/llm/failures/README.md", "tests/llm/safety/README.md",
    "tests/llm/eval/README.md", "tests/voice/README.md", "tools/README.md", "_dev/README.md", "docs/README.md",
    "docs/decisions/README.md",
)
SHARED_NODE_READMES = ("reasoning/README.md", "memory/README.md", "llm/README.md", "llm/persona/README.md",
                       "voice/README.md", "safety/README.md", "comms/README.md")
IDENTITY = {  # plan section 1.3
    "incubator-1": dict(machine="incubator", speaks=True, voice=True, server="incubator-1", slot=0,
                        model="models/llm/Qwen-3.5-2b-roleplay-tuned.Q8_0.gguf"),
    "incubator-2": dict(machine="incubator", speaks=False, voice=False, server="incubator-2", slot=0,
                        model="models/llm/Qwen3-0.6B-Q8_0.gguf"),
    "hvac": dict(machine="hvac", speaks=False, voice=False, server="hvac", slot=0, model="models/llm/Qwen3-0.6B-Q8_0.gguf"),
    "animal-protection": dict(machine="animal_protection", speaks=False, voice=False, server="animal-protection", slot=0,
                              model="models/llm/Qwen3-0.6B-Q8_0.gguf"),
}
NOT_OURS = (".venv", "interface/web/node_modules", "interface/web/dist", "_dev", ".git", ".cache", ".pytest_cache",
            ".hypothesis", "logs", "models", "third_party/llama.cpp/bin", "third_party/nats-server/bin")
MODELS_PRESENT = (ROOT / "models" / "llm" / "Qwen3-0.6B-Q8_0.gguf").is_file()
needs_models = pytest.mark.skipif(not MODELS_PRESENT, reason="models not installed on this machine (see models/README.md)")


def _ours(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return not any(rel == skip or rel.startswith(skip + "/") for skip in NOT_OURS) and "__pycache__" not in rel


def _node_config(node: str) -> dict[str, object]:
    return tomllib.loads((ROOT / "nodes" / node / "node.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("readme", FOLDER_READMES)
def test_every_planned_folder_has_its_readme(readme: str) -> None:
    assert (ROOT / readme).is_file(), readme


@pytest.mark.parametrize("node", NODES)
def test_every_node_has_its_parts_and_readmes(node: str) -> None:
    base = ROOT / "nodes" / node
    for readme in ("README.md", *SHARED_NODE_READMES):
        assert (base / readme).is_file(), f"{node}/{readme}"
    assert (base / "node.toml").is_file() and (base / "memory" / "rom").is_dir()


@pytest.mark.parametrize("node", NODES)
def test_node_identity_matches_section_1_3(node: str) -> None:
    config = _node_config(node)
    expected = IDENTITY[node]
    llm = config["llm"]
    assert isinstance(llm, dict)
    assert config["schema_version"] == 1 and config["id"] == node
    assert (config["machine"], config["speaks"], config["voice"]) == (expected["machine"], expected["speaks"],
                                                                    expected["voice"])
    assert (llm["server"], llm["slot"], llm["model"]) == (expected["server"], expected["slot"], expected["model"])


@needs_models
@pytest.mark.parametrize("node", NODES)
def test_node_models_are_installed(node: str) -> None:
    config = _node_config(node)
    llm, embeddings = config["llm"], config["embeddings"]
    assert isinstance(llm, dict) and isinstance(embeddings, dict)
    assert (ROOT / str(llm["model"])).is_file()
    assert (ROOT / str(embeddings["model"]) / "model.onnx").is_file()


def test_exactly_one_node_speaks_for_jarvis() -> None:
    assert [n for n in NODES if _node_config(n)["speaks"]] == ["incubator-1"]


def test_the_shared_readmes_are_identical_in_all_four() -> None:
    for readme in SHARED_NODE_READMES:
        texts = {(ROOT / "nodes" / node / readme).read_bytes() for node in NODES}
        assert len(texts) == 1, f"{readme} differs between the nodes"


def test_ports_match_section_4_1() -> None:
    ports = tomllib.loads((ROOT / "launcher" / "ports.toml").read_text(encoding="utf-8"))
    assert ports["host"] == "127.0.0.1"
    assert (ports["nats"], ports["matrix_api"], ports["gateway"], ports["website"]) == (4222, 8000, 8010, 5173)
    assert ports["llm"] == {"incubator-1": 8081, "incubator-2": 8082, "hvac": 8083, "animal-protection": 8084}
    assert ports["mcp"] == {"incubator-1": 8101, "incubator-2": 8102, "hvac": 8103, "animal-protection": 8104}
    flat = [v for k, v in ports.items() if isinstance(v, int) and k != "schema_version"]
    flat += [*ports["llm"].values(), *ports["mcp"].values()]
    assert len(flat) == len(set(flat)), "two services share a port"


def test_no_empty_stub_files() -> None:
    stubs = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*")
             if p.is_file() and _ours(p) and p.stat().st_size == 0]
    assert stubs == []


@pytest.mark.parametrize("part", ("reasoning", "safety"))
def test_frozen_parts_hold_only_their_readme_until_6d(part: str) -> None:
    for node in NODES:
        files = [p.relative_to(ROOT).as_posix() for p in (ROOT / "nodes" / node / part).rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts]
        assert files == [f"nodes/{node}/{part}/README.md"]


def test_memory_holds_no_code_until_6d() -> None:
    for node in NODES:
        memory = ROOT / "nodes" / node / "memory"
        code = [p.name for p in memory.rglob("*") if p.is_file() and p.suffix not in (".md", ".json")]
        assert code == [], f"{node}/memory has code before 6D: {code}"


@pytest.mark.parametrize("path", ("models/llm/Qwen3-0.6B-Q8_0.gguf", ".venv/pyvenv.cfg", "logs/x.jsonl",
                                  "nodes/incubator-1/data/cmm.sqlite", "third_party/llama.cpp/bin/llama-server.exe",
                                  "interface/web/node_modules/x", ".cache/old_name_scan.json", ".hypothesis/x"))
def test_big_or_generated_files_stay_out_of_git(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    assert result.returncode == 0, f"{path} is not ignored by .gitignore"


@pytest.mark.parametrize("path", ("models/MANIFEST.json", "models/README.md", "third_party/nats-server/README.md",
                                  "nodes/incubator-1/node.toml", "nodes/hvac/memory/rom/hvac_manual.md",
                                  "requirements.lock"))
def test_small_records_stay_in_git(path: str) -> None:
    result = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
    assert result.returncode == 1, f"{path} is ignored but must be in git"
