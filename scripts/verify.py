"""Run the repository's offline verification suites.

Mirrors `.github/workflows/ci.yml` so the same checks can be run before pushing, and adds
two that CI does not run: schema-generation determinism and the retrieval acceptance test.

Default mode uses Docker, so every toolchain is available without local installs. `--local`
uses the host's toolchains and reports skipped steps honestly rather than silently passing.

A step is skipped only when its probe genuinely fails — probing the capability (can this
interpreter import pytest?) rather than the presence of a binary name, because `pytest` is
commonly installed as a module with no executable on PATH.

Exit codes: 0 when every executed step passed, 1 when any failed. Skips do not fail the run
unless `--strict` is given.

Usage:
    python scripts/verify.py
    python scripts/verify.py --local
    python scripts/verify.py --only contracts,rag-api
    python scripts/verify.py --with-db --strict
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCKER_REPO = str(REPO).replace("\\", "/")

NETWORK = "verify-net"
DB_CONTAINER = "verify-pg"
PG_IMAGE = "pgvector/pgvector:0.8.2-pg16"

PY = sys.executable
RAG_DEPS = "import sqlalchemy, pgvector, psycopg, pydantic_settings"


@dataclass
class Step:
    name: str
    description: str
    cwd: str
    probe: list[str] | None = None
    docker_image: str | None = None
    docker_cmd: list[str] = field(default_factory=list)
    local_cmds: list[list[str]] = field(default_factory=list)
    needs_db: bool = False


STEPS = [
    Step(
        name="contracts",
        description="release, gate and indicator contracts",
        cwd=".",
        docker_image="python:3.12-slim",
        docker_cmd=["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
        local_cmds=[[PY, "-m", "unittest", "discover", "-s", "tests", "-v"]],
    ),
    Step(
        name="rag-api",
        description="RAG API unit tests",
        cwd="services/rag-api",
        probe=[PY, "-c", "import pytest, sqlalchemy, pgvector, psycopg, pydantic_settings"],
        docker_image="python:3.12-slim",
        docker_cmd=["sh", "-c",
                    "pip install -q -r requirements-dev.txt && python -m pytest tests -q"],
        local_cmds=[[PY, "-m", "pytest", "tests", "-q"]],
    ),
    Step(
        name="evaluator",
        description="evaluator unit tests",
        cwd="services/evaluator",
        probe=[PY, "-m", "pytest", "--version"],
        docker_image="python:3.12-slim",
        docker_cmd=["sh", "-c",
                    "pip install -q -r requirements-dev.txt && python -m pytest tests -q"],
        local_cmds=[[PY, "-m", "pytest", "tests", "-q"]],
    ),
    Step(
        name="dotnet",
        description=".NET build and tests",
        cwd="services/dotnet-ingest",
        probe=["dotnet", "--version"],
        docker_image="mcr.microsoft.com/dotnet/sdk:8.0",
        docker_cmd=["sh", "-c",
                    "dotnet restore src/IngestApi/IngestApi.csproj && "
                    "dotnet build src/IngestApi/IngestApi.csproj --no-restore -c Release && "
                    "dotnet test tests/IngestApi.Tests/IngestApi.Tests.csproj "
                    "--no-restore -c Release"],
        local_cmds=[
            ["dotnet", "restore", "src/IngestApi/IngestApi.csproj"],
            ["dotnet", "build", "src/IngestApi/IngestApi.csproj", "--no-restore", "-c", "Release"],
            ["dotnet", "test", "tests/IngestApi.Tests/IngestApi.Tests.csproj",
             "--no-restore", "-c", "Release"],
        ],
    ),
    Step(
        name="node",
        description="TypeScript typecheck, build and tests",
        cwd="services/gateway",
        probe=["npm", "--version"],
        docker_image="node:22-alpine",
        docker_cmd=["sh", "-c", "npm ci && npm run typecheck && npm run build && npm test"],
        local_cmds=[
            ["npm", "ci"],
            ["npm", "run", "typecheck"],
            ["npm", "run", "build"],
            ["npm", "test"],
        ],
    ),
    Step(
        name="ddl",
        description="schema generation is deterministic",
        cwd=".",
        probe=[PY, "-c", RAG_DEPS],
        docker_image="python:3.12-slim",
        docker_cmd=["sh", "-c",
                    "pip install -q -r services/rag-api/requirements.txt && "
                    "a=$(python scripts/experiments/parity.py ddl | grep -o 'sha256[^,]*') && "
                    "b=$(python scripts/experiments/parity.py ddl | grep -o 'sha256[^,]*') && "
                    "test \"$a\" = \"$b\" && echo \"stable: $a\""],
        local_cmds=[[PY, "-c",
                     "import subprocess,sys;"
                     "run=lambda: subprocess.run([sys.executable,'scripts/experiments/parity.py',"
                     "'ddl'],capture_output=True,text=True).stdout;"
                     "a,b=run(),run();"
                     "print('stable' if a==b else 'UNSTABLE');"
                     "sys.exit(0 if a==b else 1)"]],
    ),
    Step(
        name="retrieval",
        description="retrieval acceptance (needs a database)",
        cwd=".",
        probe=[PY, "-c", RAG_DEPS],
        docker_image="python:3.12-slim",
        docker_cmd=["sh", "-c",
                    "pip install -q -r services/rag-api/requirements.txt && "
                    "RAG_MIN_SCORE=0 DATABASE_URL=$VERIFY_DATABASE_URL "
                    "python scripts/experiments/rrf_acceptance.py"],
        local_cmds=[[PY, "scripts/experiments/rrf_acceptance.py"]],
        needs_db=True,
    ),
]


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, text=True).returncode == 0
    except OSError:
        return False


def probe_ok(probe: list[str] | None) -> tuple[bool, str]:
    if not probe:
        return True, ""
    try:
        result = subprocess.run(probe, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return False, f"{probe[0]} not found"
    if result.returncode != 0:
        return False, f"`{' '.join(probe)}` failed"
    return True, ""


def start_db() -> bool:
    subprocess.run(["docker", "network", "create", NETWORK], capture_output=True, text=True)
    subprocess.run(["docker", "rm", "-f", DB_CONTAINER], capture_output=True, text=True)
    result = subprocess.run(
        ["docker", "run", "-d", "--name", DB_CONTAINER, "--network", NETWORK,
         "-e", "POSTGRES_USER=ai", "-e", "POSTGRES_PASSWORD=ai",
         "-e", "POSTGRES_DB=aiplatform", PG_IMAGE],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False
    time.sleep(6)
    return True


def stop_db() -> None:
    subprocess.run(["docker", "rm", "-f", DB_CONTAINER], capture_output=True, text=True)
    subprocess.run(["docker", "network", "rm", NETWORK], capture_output=True, text=True)


def docker_command(step: Step) -> list[str]:
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{DOCKER_REPO}:/src",
        "-w", f"/src/{step.cwd}" if step.cwd != "." else "/src",
    ]
    if step.needs_db:
        cmd += ["--network", NETWORK,
                "-e", f"VERIFY_DATABASE_URL=postgresql+psycopg://ai:ai@{DB_CONTAINER}:5432/aiplatform"]
    return cmd + [step.docker_image] + step.docker_cmd


def show_tail(text: str) -> None:
    lines = (text or "").strip().splitlines()[-12:]
    print(f"    {'-' * 66}")
    for line in lines:
        print(f"    {line}")
    print(f"    {'-' * 66}")


def run_step(step: Step, use_docker: bool, db_up: bool) -> tuple[str, str]:
    commands = [docker_command(step)] if use_docker else step.local_cmds
    started = time.time()
    for cmd in commands:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO / step.cwd)
        except (FileNotFoundError, OSError) as exc:
            show_tail(str(exc))
            return "FAIL", "command not found"
        if result.returncode != 0:
            show_tail(result.stderr or result.stdout)
            return "FAIL", f"{time.time() - started:.1f}s"
    return "pass", f"{time.time() - started:.1f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the offline verification suites.")
    parser.add_argument("--local", action="store_true",
                        help="use host toolchains instead of Docker")
    parser.add_argument("--only", default="", help="comma-separated step names to run")
    parser.add_argument("--skip", default="", help="comma-separated step names to skip")
    parser.add_argument("--with-db", action="store_true",
                        help="also run steps that need a database")
    parser.add_argument("--strict", action="store_true",
                        help="treat skipped steps as failures")
    args = parser.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    names = {s.name for s in STEPS}
    unknown = (only | skip) - names
    if unknown:
        print(f"Unknown step(s): {', '.join(sorted(unknown))}")
        print(f"Available: {', '.join(sorted(names))}")
        return 1

    use_docker = not args.local
    if use_docker and not docker_available():
        print("Docker is not available; falling back to host toolchains.\n")
        use_docker = False

    selected = [s for s in STEPS if (not only or s.name in only) and s.name not in skip]

    db_up = False
    if args.with_db and use_docker:
        print("Starting throwaway pgvector container...")
        db_up = start_db()
        if not db_up:
            print("  could not start the database; database steps will be skipped\n")

    print(f"verify  mode={'docker' if use_docker else 'local'}  steps={len(selected)}\n")

    results: list[tuple[Step, str, str]] = []
    try:
        for step in selected:
            print(f"  {step.name:<11} {step.description}")
            if use_docker and not step.docker_image:
                results.append((step, "skipped", "no docker image"))
                print("    skipped (no docker image)")
                continue
            if step.needs_db and not db_up:
                results.append((step, "skipped", "needs --with-db"))
                print("    skipped (needs --with-db)")
                continue
            ok, reason = (True, "") if use_docker else probe_ok(step.probe)
            if not ok:
                detail = reason if use_docker else f"{reason} (--local)"
                results.append((step, "skipped", detail))
                print(f"    skipped ({reason})")
                continue
            status, detail = run_step(step, use_docker, db_up)
            results.append((step, status, detail))
            print(f"    {status} ({detail})")
    finally:
        if db_up:
            stop_db()

    print(f"\n{'step':<12} {'result':<9} detail")
    print("-" * 50)
    for step, status, detail in results:
        print(f"{step.name:<12} {status:<9} {detail}")

    failed = [s.name for s, status, _ in results if status == "FAIL"]
    skipped = [s.name for s, status, _ in results if status == "skipped"]

    print()
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    if skipped and args.strict:
        print(f"STRICT: skipped {', '.join(skipped)}")
        return 1
    if skipped:
        print(f"passed with {len(skipped)} skipped: {', '.join(skipped)}")
        print("Run without --local for the full set.")
        return 0
    print("all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
