#!/usr/bin/env python3
"""Install/control this project's local, native GROBID CRF service.

The service includes full academic structure, header and bibliography parsing.
It listens only on 127.0.0.1, without Docker, shell-profile edits or launch daemon.
The project's request adapter disables external metadata consolidation per request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import tarfile
import time
import urllib.request
from pathlib import Path

import psutil
import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.9.1"
INSTALL = ROOT / "data" / "services" / "grobid"
SOURCE = INSTALL / f"grobid-{VERSION}"
RUNTIME = INSTALL / "runtime"
PIDFILE = RUNTIME / "service.json"
URL = "http://127.0.0.1:8070"
SOURCE_URL = f"https://github.com/grobidOrg/grobid/archive/refs/tags/{VERSION}.tar.gz"
SOURCE_SHA256 = "e5fc91e2231d2ef0ae4e7f9d098cd84a702fb14fc823d2f70ae048481f15c374"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def java_home() -> Path:
    values = [
        os.environ.get("GROBID_JAVA_HOME"),
        os.environ.get("JAVA_HOME"),
        "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
        "/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
        "/usr/lib/jvm/java-21-openjdk-amd64",
        "/usr/lib/jvm/java-21-openjdk-arm64",
    ]
    for value in values:
        if value and (Path(value) / "bin" / "java").is_file():
            return Path(value).resolve()
    raise RuntimeError("JDK21 required. macOS: brew install openjdk@21; or set GROBID_JAVA_HOME.")


def native_dir() -> str:
    arch = platform.machine().lower()
    if platform.system() == "Darwin":
        return "mac_arm-64" if arch in {"arm64", "aarch64"} else "mac-64"
    if platform.system() == "Linux":
        return "lin_arm-64" if arch in {"arm64", "aarch64"} else "lin-64"
    raise RuntimeError("This native service helper supports macOS and Linux")


def health() -> bool:
    try:
        with urllib.request.urlopen(URL + "/api/isalive", timeout=2) as response:
            return response.status == 200 and response.read().strip().lower() == b"true"
    except (OSError, ValueError):
        return False


def owned_process():
    if not PIDFILE.exists():
        return None
    state = json.loads(PIDFILE.read_text())
    try:
        proc = psutil.Process(state["pid"])
        if abs(proc.create_time() - state["process_created_at"]) > 0.1:
            return None
        if not any(str(SOURCE) in arg for arg in proc.cmdline()):
            return None
        return proc if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE else None
    except (psutil.Error, KeyError):
        return None


def install():
    jdk = java_home()
    INSTALL.mkdir(parents=True, exist_ok=True)
    archive = INSTALL / f"grobid-{VERSION}.tar.gz"
    if not archive.exists():
        part = archive.with_suffix(".tar.gz.part")
        with urllib.request.urlopen(SOURCE_URL, timeout=120) as response, part.open("wb") as out:
            while block := response.read(1024 * 1024):
                out.write(block)
        os.replace(part, archive)
    if digest(archive) != SOURCE_SHA256:
        raise RuntimeError("GROBID source archive SHA256 differs from the tested release archive")
    if not SOURCE.exists():
        with tarfile.open(archive, "r:gz") as bundle:
            # Python3.11.16+ supports the data filter; prohibit archive escapes.
            bundle.extractall(INSTALL, filter="data")
    if " " in str(SOURCE):
        raise ValueError("GROBID/Wapiti requires a source directory with no spaces")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    distribution = SOURCE / "grobid-service" / "build" / "install" / "grobid-service"
    if distribution.exists() and not (distribution / "bin" / "grobid-service").is_file():
        distribution.rename(distribution.with_name("grobid-service.incomplete-" + str(int(time.time()))))
    # Upstream0.9.1 sets EXCLUDE for distZip but omits installDist. Gradle9
    # rejects duplicated local/Maven langdetect jars without this matching rule.
    init_script = RUNTIME / "distribution-compat.init.gradle"
    init_script.write_text(
        "gradle.projectsEvaluated {\n"
        "  rootProject.project(':grobid-service').tasks.named('installDist') {\n"
        "    duplicatesStrategy = org.gradle.api.file.DuplicatesStrategy.EXCLUDE\n"
        "  }\n"
        "}\n"
    )
    env = dict(os.environ, JAVA_HOME=str(jdk))
    env.pop("VIRTUAL_ENV", None)  # The CRF service does not embed Python/JEP.
    with (RUNTIME / "build.log").open("w") as log:
        subprocess.run(
            [
                str(SOURCE / "gradlew"),
                "--no-daemon",
                "--init-script",
                str(init_script),
                "--max-workers=2",
                "-Dorg.gradle.jvmargs=-Xmx1024m",
                "-Pkotlin.compiler.execution.strategy=in-process",
                ":grobid-service:installDist",
                "-x",
                "test",
            ],
            cwd=SOURCE,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
        )
    version = subprocess.run(
        [str(jdk / "bin" / "java"), "-version"], capture_output=True, text=True, check=True
    )
    manifest = {
        "version": VERSION,
        "source_url": SOURCE_URL,
        "source_archive_sha256": digest(archive),
        "java": version.stderr.strip(),
        "platform": platform.platform(),
        "engine": "Wapiti CRF",
        "full_academic_service": True,
        "java_heap_mb": 1536,
        "concurrency": 1,
        "native_library": native_dir(),
        "installed_at_unix": time.time(),
    }
    save(INSTALL / "installation.json", manifest)
    return manifest


def start():
    existing = owned_process()
    if existing:
        return {"status": "already_running", "pid": existing.pid, "healthy": health(), "url": URL}
    if health():
        raise RuntimeError("Port8070 is occupied by a service not started by this helper")
    jars = SOURCE / "grobid-service" / "build" / "install" / "grobid-service" / "lib"
    if not jars.is_dir():
        raise RuntimeError("Run this helper's install command before start")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    settings = yaml.safe_load((SOURCE / "grobid-home" / "config" / "grobid.yaml").read_text())
    settings["grobid"].update(grobidHome=str(SOURCE / "grobid-home"), concurrency=1, modelPreload=False)
    settings["grobid"]["pdf"]["pdfalto"].update(memoryLimitMb=1024, timeoutSec=120)
    for group in ("applicationConnectors", "adminConnectors"):
        for connector in settings["server"][group]:
            connector["bindHost"] = "127.0.0.1"
    config = RUNTIME / "grobid.local.yaml"
    config.write_text(yaml.safe_dump(settings, sort_keys=False))
    jdk = java_home()
    args = [
        str(jdk / "bin" / "java"),
        "-Xms128m",
        "-Xmx1536m",
        "-XX:MaxDirectMemorySize=256m",
        "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "-Dorg.grobid.config=" + str(config),
        "-Djava.library.path=" + str(SOURCE / "grobid-home" / "lib" / native_dir()),
        "-cp",
        str(jars / "*"),
        "org.grobid.service.main.GrobidServiceApplication",
        "server",
        str(config),
    ]
    with (RUNTIME / "service.log").open("ab") as log:
        process = subprocess.Popen(
            args,
            cwd=SOURCE,
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    proc = psutil.Process(process.pid)
    save(
        PIDFILE,
        {
            "pid": process.pid,
            "process_created_at": proc.create_time(),
            "url": URL,
            "version": VERSION,
            "config": str(config),
            "started_at_unix": time.time(),
        },
    )
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"GROBID exited {process.returncode}; see {RUNTIME / 'service.log'}")
        if health():
            return {
                "status": "running",
                "pid": process.pid,
                "healthy": True,
                "url": URL,
                "log": str(RUNTIME / "service.log"),
                "rss_mb": round(proc.memory_info().rss / 1024**2, 1),
            }
        time.sleep(0.5)
    raise RuntimeError("GROBID startup timed out; process retained for diagnostics, use stop when finished")


def stop():
    proc = owned_process()
    if not proc:
        return {"status": "not_running"}
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=15)
    except psutil.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
    PIDFILE.unlink(missing_ok=True)
    return {"status": "stopped", "pid": proc.pid}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "start", "stop", "status"))
    command = parser.parse_args().command
    if command == "status":
        proc = owned_process()
        result = {
            "running": bool(proc),
            "healthy": health(),
            "url": URL,
            "pid": proc.pid if proc else None,
            "rss_mb": round(proc.memory_info().rss / 1024**2, 1) if proc else None,
        }
    else:
        result = {"install": install, "start": start, "stop": stop}[command]()
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
