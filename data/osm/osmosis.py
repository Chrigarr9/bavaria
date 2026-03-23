import subprocess as sp
import shutil
import os
import time
import threading
import queue
from pathlib import Path


def configure(context):
    context.config("osmosis_binary", "osmosis")

    context.config("java_binary", "java")
    context.config("java_memory", "50G")

    # Emit periodic progress lines even if osmosis itself stays quiet.
    # Set to 0 to disable.
    context.config("osmosis_heartbeat_interval_s", 60)

    # If osmosis tasks don't print any progress, enabling this typically prints element counts.
    # Set to 0 to disable.
    context.config("osmosis_log_progress_interval_s", 60)


def _inject_log_progress(arguments, interval_s: int):
    if not interval_s or interval_s <= 0:
        return arguments

    if "--log-progress" in arguments:
        return arguments

    # Insert right after the first read-* task (and its parameters), which is where Osmosis expects it.
    result = []
    i = 0
    injected = False

    while i < len(arguments):
        token = arguments[i]
        result.append(token)
        i += 1

        if (not injected) and token.startswith("--read-"):
            while i < len(arguments) and not str(arguments[i]).startswith("--"):
                result.append(arguments[i])
                i += 1

            result += ["--log-progress", f"interval={interval_s}"]
            injected = True

    return result


def _guess_output_path(arguments, cwd):
    # Try to find an output file to report its growing size in the heartbeat.
    # Works for common cases like: --write-pbf file=foo.pbf OR --write-pbf foo.pbf
    tasks = {"--write-pbf", "--write-xml", "--write-fast",
             "--write-fast-xml", "--write-apidb"}
    for i, token in enumerate(arguments):
        if token not in tasks:
            continue

        j = i + 1
        while j < len(arguments) and not str(arguments[j]).startswith("--"):
            item = str(arguments[j])
            if item.startswith("file="):
                candidate = item.split("=", 1)[1]
                p = Path(candidate)
                return p if p.is_absolute() else Path(cwd) / p
            if item.endswith((".pbf", ".osm", ".osm.gz", ".xml", ".xml.gz")):
                p = Path(item)
                return p if p.is_absolute() else Path(cwd) / p
            j += 1

    return None


def run(context, arguments=[], cwd=None):
    """
        This function calls osmosis.
    """
    # Make sure there is a dependency
    context.stage("data.osm.osmosis")

    if cwd is None:
        cwd = context.path()

    # Optionally inject --log-progress to avoid multi-hour silence.
    try:
        log_progress_interval_s = int(
            context.config("osmosis_log_progress_interval_s"))
    except Exception:
        log_progress_interval_s = 0

    arguments = _inject_log_progress(arguments, log_progress_interval_s)

    # Prepare command line
    command_line = [
        shutil.which(context.config("osmosis_binary"))
    ] + arguments

    # Prepare environment
    environment = os.environ.copy()
    environment["JAVACMD"] = shutil.which(context.config("java_binary"))
    environment["JAVACMD_OPTIONS"] = "-Xmx%s" % context.config("java_memory")

    # Run Osmosis with verbose logging
    print(f"[OSMOSIS] Starting with command: {' '.join(command_line)}")
    print(f"[OSMOSIS] Working directory: {cwd}")
    print(f"[OSMOSIS] Java memory: {context.config('java_memory')}")

    output_path = _guess_output_path(arguments, cwd)

    # Use Popen but keep a heartbeat, because Osmosis can be silent for hours.
    process = sp.Popen(
        command_line,
        cwd=cwd,
        env=environment,
        stdout=sp.PIPE,
        stderr=sp.STDOUT,
        text=True,
        encoding="latin-1",
        bufsize=1,
    )

    output_queue: "queue.Queue[str]" = queue.Queue()

    def _reader():
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if not line:
                break
            output_queue.put(line)

    threading.Thread(target=_reader, daemon=True).start()

    try:
        heartbeat_interval_s = int(
            context.config("osmosis_heartbeat_interval_s"))
    except Exception:
        heartbeat_interval_s = 60
    if heartbeat_interval_s <= 0:
        heartbeat_interval_s = None

    started = time.monotonic()
    last_any_output = started

    while True:
        try:
            timeout = heartbeat_interval_s if heartbeat_interval_s is not None else 3600
            line = output_queue.get(timeout=timeout)
            last_any_output = time.monotonic()
            print(f"[OSMOSIS] {line.rstrip()}")
        except queue.Empty:
            if process.poll() is not None:
                break

            elapsed_s = int(time.monotonic() - started)
            silent_s = int(time.monotonic() - last_any_output)
            extra = ""
            if output_path is not None and output_path.exists():
                try:
                    size_bytes = output_path.stat().st_size
                    size_gib = size_bytes / (1024**3)
                    extra = f"; output={output_path.name} {size_gib:.2f} GiB"
                except Exception:
                    pass

            print(
                f"[OSMOSIS] still running... elapsed={elapsed_s}s; silent={silent_s}s{extra}")

    return_code = process.wait()

    if not return_code == 0:
        raise RuntimeError("Osmosis return code: %d" % return_code)

    print("[OSMOSIS] Completed successfully")


def validate(context):
    if shutil.which(context.config("osmosis_binary")) in ["", None]:
        raise RuntimeError("Cannot find Osmosis binary at: %s" %
                           context.config("osmosis_binary"))

    if not b"0.48." in sp.check_output([
        shutil.which(context.config("osmosis_binary")),
        "-v"
    ], stderr=sp.STDOUT):
        print("WARNING! Osmosis of at least version 0.48.x is recommended!")


def execute(context):
    pass
