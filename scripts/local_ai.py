#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "python-dotenv>=1.0",
#   "rich>=13.9",
# ]
# ///
"""Small setup/lifecycle helper for the local-ai service recipes."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import tomllib
from dotenv import dotenv_values
from rich.console import Console
from rich.table import Table

console = Console()
err_console = Console(stderr=True)
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LocalAIError(RuntimeError):
    pass


def repo_root_from_service(service_dir: Path) -> Path:
    service_dir = service_dir.resolve()
    if service_dir.parent.name != "services":
        raise LocalAIError(f"Expected a directory under services/: {service_dir}")
    return service_dir.parent.parent


def load_manifest(service_dir: Path) -> dict[str, Any]:
    path = service_dir / "service.toml"
    if not path.is_file():
        raise LocalAIError(f"Missing service manifest: {path}")
    with path.open("rb") as file:
        return tomllib.load(file)


def parse_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values = dotenv_values(path)
    return {key: value for key, value in values.items() if value is not None}



def validate_env_syntax(path: Path) -> None:
    for lineno, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise LocalAIError(f"{path}:{lineno}: expected KEY=VALUE")
        key = line.split("=", 1)[0].strip()
        if not ENV_KEY_RE.match(key):
            raise LocalAIError(f"{path}:{lineno}: invalid environment key {key!r}")


def render_template_with_existing(
    template_path: Path, existing_path: Path, *, drop_keys: set[str] | None = None
) -> str:
    text = template_path.read_text()
    if not existing_path.is_file():
        return text
    existing = parse_env(existing_path)
    for key in drop_keys or set():
        existing.pop(key, None)
    seen: set[str] = set()
    rendered: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        probe = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        if probe and not probe.startswith("#") and "=" in probe:
            key = probe.split("=", 1)[0].strip()
            if ENV_KEY_RE.match(key):
                seen.add(key)
                # Schema version markers come from the checked-in template; all
                # other configured values preserve the user's current choice.
                if key in existing and not key.endswith("_CONFIG_VERSION"):
                    prefix = "export " if stripped.startswith("export ") else ""
                    rendered.append(f"{prefix}{key}={existing[key]}")
                    continue
        rendered.append(raw_line)
    extras = sorted(set(existing) - seen)
    if extras:
        rendered += ["", "# Existing local keys not present in the current template."]
        rendered += [f"{key}={existing[key]}" for key in extras]
    return "\n".join(rendered).rstrip() + "\n"


def ensure_editor_waits(command: list[str]) -> list[str]:
    """Add the foreground/wait flag for editors that detach by default."""
    if not command:
        return command

    executable = Path(command[0]).name.lower()
    args = command[1:]

    if executable in {"gvim", "gvimdiff", "mvim", "mvimdiff"}:
        if "-f" not in args and "--nofork" not in args:
            return [command[0], "-f", *args]
    elif executable in {"code", "code-insiders", "codium", "cursor", "subl", "sublime_text", "gedit"}:
        if "--wait" not in args:
            return [*command, "--wait"]
    elif executable == "kate":
        if "--block" not in args:
            return [*command, "--block"]
    elif executable in {"vim", "vimdiff"} and ("-g" in args or "--gui" in args):
        if "-f" not in args and "--nofork" not in args:
            return [command[0], "-f", *args]

    return command


def choose_editor() -> list[str]:
    value = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if value:
        return ensure_editor_waits(shlex.split(value))
    for candidate in ("vim", "vi", "nano"):
        path = shutil.which(candidate)
        if path:
            return ensure_editor_waits([path])
    raise LocalAIError("No editor found. Set $VISUAL or $EDITOR.")


def install_env_from_template(
    destination: Path,
    template: Path,
    *,
    label: str,
    force_edit: bool,
    accept_defaults: bool,
    drop_keys: set[str] | None = None,
) -> None:
    if not template.is_file():
        raise LocalAIError(f"Missing configuration template: {template}")

    existing_values = parse_env(destination)
    template_values = parse_env(template)
    existing_keys = set(existing_values)
    expected_keys = set(template_values)
    missing_keys = expected_keys - existing_keys
    version_mismatch = {
        key for key in expected_keys
        if key.endswith("_CONFIG_VERSION")
        and existing_values.get(key) != template_values.get(key)
    }
    cleanup_keys = (drop_keys or set()) & existing_keys
    needs_refresh = (
        force_edit
        or not destination.is_file()
        or bool(missing_keys)
        or bool(version_mismatch)
        or bool(cleanup_keys)
    )
    if not needs_refresh:
        return

    if destination.is_file() and (missing_keys or version_mismatch or cleanup_keys):
        reasons = []
        if missing_keys:
            reasons.append("new keys: " + ", ".join(sorted(missing_keys)))
        if version_mismatch:
            reasons.append("schema version update: " + ", ".join(sorted(version_mismatch)))
        if cleanup_keys:
            reasons.append("redundant inherited keys: " + ", ".join(sorted(cleanup_keys)))
        console.print(f"[yellow]Refreshing {label}[/yellow] ({'; '.join(reasons)})")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staged_text = render_template_with_existing(template, destination, drop_keys=drop_keys)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.staging-", dir=destination.parent, text=True
    )
    stage = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(staged_text)
        os.chmod(stage, 0o600)

        if accept_defaults:
            console.print(f"[cyan]Using staged defaults for {label}.[/cyan]")
        else:
            if not sys.stdin.isatty():
                raise LocalAIError(
                    f"{label} needs configuration but no interactive terminal is available. "
                    "Run setup interactively or pass --accept-defaults."
                )
            editor = choose_editor()
            console.print(f"[bold]Editing {label}[/bold]")
            console.print(f"Staging file: {stage}")
            completed = subprocess.run([*editor, str(stage)])
            if completed.returncode:
                raise LocalAIError(f"Editor exited with status {completed.returncode}")

        validate_env_syntax(stage)
        os.replace(stage, destination)
        os.chmod(destination, 0o600)
        console.print(f"[green]Configured[/green] {destination}")
    finally:
        if stage.exists():
            stage.unlink()


def expand_value(value: str, env: dict[str, str], *, rounds: int = 8) -> str:
    current = value
    pattern = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    for _ in range(rounds):
        changed = False

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            key = match.group(1)
            if key not in env:
                raise LocalAIError(f"Cannot expand {{{key}}} in {value!r}")
            changed = True
            return env[key]

        new = pattern.sub(repl, current)
        current = new
        if not changed:
            return current
    raise LocalAIError(f"Too many expansion rounds while resolving {value!r}")


def effective_env(repo_root: Path, service_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
    root_template = repo_root / ".env.template"
    service_template = service_dir / ".env.template"
    env: dict[str, str] = {}
    env.update(parse_env(repo_root / ".env"))
    env.update(parse_env(service_dir / ".env"))

    defaults = {str(k): str(v) for k, v in manifest.get("defaults", {}).items()}
    for _ in range(8):
        changed = False
        for key, value in defaults.items():
            if not env.get(key):
                try:
                    resolved = expand_value(value, env)
                except LocalAIError:
                    continue
                env[key] = resolved
                changed = True
        if not changed:
            break

    # Root/service configuration is authoritative for declared settings. Host
    # environment variables that are not declared here (for example HF_TOKEN)
    # still pass through to subprocesses via host_command_env().

    # Resolve placeholders in configured/default values after all layers exist.
    for _ in range(8):
        changed = False
        for key, value in list(env.items()):
            if "{" in value:
                resolved = expand_value(value, env)
                if resolved != value:
                    env[key] = resolved
                    changed = True
        if not changed:
            break
    return env


def validate_effective_env(env: dict[str, str], manifest: dict[str, Any]) -> None:
    required = [str(x) for x in manifest.get("config", {}).get("required", [])]
    for key in required:
        if not env.get(key):
            raise LocalAIError(f"Required configuration value is empty: {key}")

    for key, value in env.items():
        if key.endswith("_PORT") and value:
            try:
                port = int(value)
            except ValueError as ex:
                raise LocalAIError(f"{key} must be an integer, got {value!r}") from ex
            if not (1 <= port <= 65535):
                raise LocalAIError(f"{key} must be between 1 and 65535")
        if key.endswith("_ROOT") and value and not Path(value).is_absolute():
            raise LocalAIError(f"{key} must be an absolute path, got {value!r}")
        if key.endswith("_GPU") and value and value != "auto":
            try:
                int(value)
            except ValueError as ex:
                raise LocalAIError(f"{key} must be an integer GPU index or 'auto'") from ex


def resolve(text: str, env: dict[str, str]) -> str:
    return expand_value(text, env)


def service_name(manifest: dict[str, Any]) -> str:
    return str(manifest["service"]["name"])


def selected_model_names(manifest: dict[str, Any], env: dict[str, str], extra: list[str]) -> set[str]:
    selected = {
        str(model["name"])
        for model in manifest.get("models", [])
        if bool(model.get("required", False))
    }
    selection_var = manifest.get("model_selection", {}).get("env")
    if selection_var:
        raw = env.get(str(selection_var), "")
        selected.update(item.strip() for item in raw.split(",") if item.strip())
    selected.update(extra)
    return selected


def model_destination(model: dict[str, Any], env: dict[str, str]) -> Path:
    return Path(resolve(str(model["destination"]), env))


def model_complete(model: dict[str, Any], env: dict[str, str]) -> bool:
    root = model_destination(model, env)
    for rel in model.get("check_files", []):
        if not (root / str(rel)).is_file():
            return False
    for rel in model.get("check_dirs", []):
        if not (root / str(rel)).is_dir():
            return False
    if not model.get("check_files") and not model.get("check_dirs"):
        return root.exists() and any(root.iterdir()) if root.is_dir() else root.exists()
    return True


def model_missing(model: dict[str, Any], env: dict[str, str]) -> list[str]:
    root = model_destination(model, env)
    missing: list[str] = []
    for rel in model.get("check_files", []):
        path = root / str(rel)
        if not path.is_file():
            missing.append(str(path))
    for rel in model.get("check_dirs", []):
        path = root / str(rel)
        if not path.is_dir():
            missing.append(str(path) + "/")
    return missing


def host_command_env(env: dict[str, str]) -> dict[str, str]:
    merged = os.environ.copy()
    merged.update(env)
    return merged


def run_command(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    console.print("[dim]$ " + shlex.join(args) + "[/dim]")
    return subprocess.run(
        args,
        cwd=cwd,
        env=host_command_env(env),
        text=True,
        check=check,
    )


def check_prerequisites() -> None:
    missing = [cmd for cmd in ("uv", "docker", "nvidia-smi") if not shutil.which(cmd)]
    if missing:
        raise LocalAIError("Missing required host command(s): " + ", ".join(missing))
    result = subprocess.run(
        ["docker", "compose", "version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode:
        raise LocalAIError("Docker Compose plugin is not available")


def directory_rows(manifest: dict[str, Any], env: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in manifest.get("directories", []):
        rows.append(
            {
                "label": str(item.get("label", item["path"])),
                "path": resolve(str(item["path"]), env),
                "role": str(item.get("role", "")),
                "retention": str(item.get("retention", "")),
            }
        )
    return rows


def print_plan(
    service_dir: Path,
    manifest: dict[str, Any],
    env: dict[str, str],
    selected_models: set[str],
) -> None:
    meta = manifest["service"]
    console.rule(f"[bold]{meta.get('display_name', meta['name'])}[/bold]")
    console.print(str(meta.get("description", "")))

    network = Table(title="Network / runtime", show_header=False, box=None)
    network.add_column("key", style="bold")
    network.add_column("value")
    bind_var = str(meta.get("bind_var", "LOCAL_AI_BIND_ADDRESS"))
    port_var = str(meta.get("port_var", ""))
    if port_var:
        network.add_row("URL", f"http://{env.get(bind_var, '127.0.0.1')}:{env.get(port_var, '?')}")
    gpu_var = meta.get("gpu_var")
    if gpu_var:
        network.add_row("GPU", env.get(str(gpu_var), "?"))
    network.add_row("Service dir", str(service_dir))
    console.print(network)

    paths = Table(title="Host storage", box=None)
    paths.add_column("Location", style="bold")
    paths.add_column("Host path", overflow="fold")
    paths.add_column("Role")
    paths.add_column("Retention")
    for row in directory_rows(manifest, env):
        paths.add_row(row["label"], row["path"], row["role"], row["retention"])
    console.print(paths)

    if manifest.get("models"):
        console.print("[bold]Model / weight locations[/bold]")
        for model in manifest.get("models", []):
            name = str(model["name"])
            required = bool(model.get("required", False))
            selected = name in selected_models
            if model_complete(model, env):
                status = "present"
            elif selected:
                status = "missing -> provision during setup"
            else:
                status = "not selected"
            policy = "required" if required else "optional"
            source = resolve(str(model.get("source_label", model.get("source", ""))), env)
            destination = str(model_destination(model, env))
            console.print(f"  [bold]{model.get('label', name)}[/bold]")
            console.print(f"    source:      {source}")
            console.print(f"    host:        {destination}")
            if model.get("container_path"):
                console.print(f"    container:   {model['container_path']}")
            console.print(f"    policy:      {policy}")
            console.print(f"    status:      {status}")


def ensure_directories(manifest: dict[str, Any], env: dict[str, str]) -> None:
    for row in directory_rows(manifest, env):
        path = Path(row["path"])
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise LocalAIError(
                f"Cannot create {path}: {ex}. Edit the configured root or create/chown the host path."
            ) from ex


def build_service(service_dir: Path, manifest: dict[str, Any], env: dict[str, str]) -> None:
    target = str(manifest["service"].get("compose_build_target", manifest["service"]["compose_service"]))
    run_command(["docker", "compose", "build", target], cwd=service_dir, env=env)


def download_huggingface(model: dict[str, Any], service_dir: Path, env: dict[str, str]) -> None:
    destination = model_destination(model, env)
    destination.mkdir(parents=True, exist_ok=True)
    args = [
        "uvx",
        "--from",
        "huggingface_hub",
        "hf",
        "download",
        str(model["repository"]),
        "--local-dir",
        str(destination),
    ]
    include = [str(x) for x in model.get("include", [])]
    if include:
        args.append("--include")
        args.extend(include)
    download_env = dict(env)
    download_env["HF_XET_HIGH_PERFORMANCE"] = "1"
    run_command(args, cwd=service_dir, env=download_env)


def download_compose(model: dict[str, Any], service_dir: Path, env: dict[str, str]) -> None:
    compose_service = str(model["compose_service"])
    command = [resolve(str(x), env) for x in model["command"]]
    args = ["docker", "compose", "run", "--rm", "--no-deps", compose_service, *command]
    run_command(args, cwd=service_dir, env=env)


def provision_model(model: dict[str, Any], service_dir: Path, env: dict[str, str]) -> None:
    if model_complete(model, env):
        console.print(f"[green]Present[/green] {model.get('label', model['name'])}")
        return
    source = str(model["source"])
    console.print(
        f"[bold]Provisioning[/bold] {model.get('label', model['name'])} -> {model_destination(model, env)}"
    )
    if source == "huggingface":
        download_huggingface(model, service_dir, env)
    elif source == "compose-command":
        download_compose(model, service_dir, env)
    else:
        raise LocalAIError(f"Unsupported model source: {source}")
    missing = model_missing(model, env)
    if missing:
        details = "\n".join(f"  - {path}" for path in missing)
        raise LocalAIError(
            f"Model provisioning finished but expected files are still missing for {model['name']}:\n{details}"
        )


def prepare_runtime_env(service_dir: Path, manifest: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    result = dict(env)
    resolver = manifest.get("runtime_resolver")
    if not resolver:
        return result
    when_var = str(resolver["when_var"])
    when_value = str(resolver["when_value"])
    if result.get(when_var) == when_value:
        command = [resolve(str(x), result) for x in resolver["command"]]
        completed = subprocess.run(
            command,
            cwd=service_dir,
            env=host_command_env(result),
            text=True,
            capture_output=True,
            check=True,
        )
        resolved = completed.stdout.strip()
        if not resolved:
            raise LocalAIError(f"Runtime resolver returned an empty value: {shlex.join(command)}")
        result[str(resolver["set_var"])] = resolved
    return result


def require_config(repo_root: Path, service_dir: Path) -> None:
    pairs = [
        (repo_root / ".env", repo_root / ".env.template"),
        (service_dir / ".env", service_dir / ".env.template"),
    ]
    missing = [path for path, _ in pairs if not path.is_file()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise LocalAIError(
            "Service configuration has not been initialized:\n"
            f"{formatted}\n\nRun ./setup.sh from the service directory."
        )
    stale: list[str] = []
    for current, template in pairs:
        current_values = parse_env(current)
        template_values = parse_env(template)
        missing_keys = set(template_values) - set(current_values)
        version_mismatch = {
            key for key in template_values
            if key.endswith("_CONFIG_VERSION")
            and current_values.get(key) != template_values.get(key)
        }
        if missing_keys:
            stale.append(f"{current}: missing {', '.join(sorted(missing_keys))}")
        if version_mismatch:
            stale.append(f"{current}: stale {', '.join(sorted(version_mismatch))}")
    if stale:
        details = "\n".join(f"  - {item}" for item in stale)
        raise LocalAIError(
            "Service configuration uses an older schema:\n"
            f"{details}\n\nRun ./setup.sh to stage and review the updated configuration."
        )


def cmd_setup(args: argparse.Namespace) -> None:
    service_dir = Path(args.service_dir).resolve()
    repo_root = repo_root_from_service(service_dir)
    manifest = load_manifest(service_dir)

    install_env_from_template(
        repo_root / ".env",
        repo_root / ".env.template",
        label="shared local-ai machine configuration",
        force_edit=args.edit or args.edit_root,
        accept_defaults=args.accept_defaults,
    )
    root_values = parse_env(repo_root / ".env")
    existing_service_values = parse_env(service_dir / ".env")
    shared_policy_keys = {
        "HF_REPOS_ROOT",
        "LOCAL_AI_SERVICE_ROOT",
        "LOCAL_AI_WORKSPACES_ROOT",
        "LOCAL_AI_BIND_ADDRESS",
    }
    redundant_shared_keys = {
        key for key in shared_policy_keys
        if key in existing_service_values
        and key in root_values
        and existing_service_values[key] == root_values[key]
    }
    install_env_from_template(
        service_dir / ".env",
        service_dir / ".env.template",
        label=f"{service_name(manifest)} service configuration",
        force_edit=args.edit or args.edit_service,
        accept_defaults=args.accept_defaults,
        drop_keys=redundant_shared_keys,
    )

    env = effective_env(repo_root, service_dir, manifest)
    validate_effective_env(env, manifest)
    selected = selected_model_names(manifest, env, args.with_model)
    known_models = {str(model["name"]) for model in manifest.get("models", [])}
    unknown = selected - known_models
    if unknown:
        raise LocalAIError("Unknown model bundle(s): " + ", ".join(sorted(unknown)))

    print_plan(service_dir, manifest, env, selected)
    if args.plan:
        return

    check_prerequisites()
    ensure_directories(manifest, env)

    # Validate Compose interpolation before doing expensive work.
    run_command(["docker", "compose", "config", "--quiet"], cwd=service_dir, env=env)

    if not args.no_build:
        build_service(service_dir, manifest, env)

    if not args.no_download:
        for model in manifest.get("models", []):
            if str(model["name"]) in selected:
                provision_model(model, service_dir, env)

    missing_required: list[str] = []
    for model in manifest.get("models", []):
        if bool(model.get("required", False)) and not model_complete(model, env):
            missing_required.extend(model_missing(model, env))
    if missing_required:
        details = "\n".join(f"  - {path}" for path in missing_required)
        raise LocalAIError("Setup is incomplete. Required model data is missing:\n" + details)

    console.print()
    console.print("[bold green]Setup complete.[/bold green]")
    console.print("Start the service with:")
    console.print("  ./start.sh")


def cmd_start(args: argparse.Namespace) -> None:
    service_dir = Path(args.service_dir).resolve()
    repo_root = repo_root_from_service(service_dir)
    manifest = load_manifest(service_dir)
    require_config(repo_root, service_dir)
    env = effective_env(repo_root, service_dir, manifest)
    validate_effective_env(env, manifest)
    env = prepare_runtime_env(service_dir, manifest, env)

    missing_dirs = [row["path"] for row in directory_rows(manifest, env) if not Path(row["path"]).is_dir()]
    if missing_dirs:
        details = "\n".join(f"  - {path}" for path in missing_dirs)
        raise LocalAIError("Service setup is incomplete. Missing directories:\n" + details + "\n\nRun ./setup.sh")

    missing_models: list[str] = []
    for model in manifest.get("models", []):
        if bool(model.get("required", False)) and not model_complete(model, env):
            missing_models.extend(model_missing(model, env))
    if missing_models:
        details = "\n".join(f"  - {path}" for path in missing_models)
        raise LocalAIError("Required model data is missing:\n" + details + "\n\nRun ./setup.sh")

    check_prerequisites()
    run_command(["docker", "compose", "config", "--quiet"], cwd=service_dir, env=env)
    target = str(manifest["service"]["compose_service"])
    run_command(["docker", "compose", "up", "-d", "--no-build", target], cwd=service_dir, env=env)

    bind_var = str(manifest["service"].get("bind_var", "LOCAL_AI_BIND_ADDRESS"))
    port_var = str(manifest["service"].get("port_var", ""))
    console.print()
    if port_var:
        console.print(f"[green]{manifest['service'].get('display_name', target)}:[/green] http://{env[bind_var]}:{env[port_var]}")


def cmd_show(args: argparse.Namespace) -> None:
    service_dir = Path(args.service_dir).resolve()
    repo_root = repo_root_from_service(service_dir)
    manifest = load_manifest(service_dir)
    require_config(repo_root, service_dir)
    env = effective_env(repo_root, service_dir, manifest)
    validate_effective_env(env, manifest)
    selected = selected_model_names(manifest, env, [])
    print_plan(service_dir, manifest, env, selected)


def cmd_model(args: argparse.Namespace) -> None:
    service_dir = Path(args.service_dir).resolve()
    repo_root = repo_root_from_service(service_dir)
    manifest = load_manifest(service_dir)
    require_config(repo_root, service_dir)
    env = effective_env(repo_root, service_dir, manifest)
    validate_effective_env(env, manifest)
    models = {str(model["name"]): model for model in manifest.get("models", [])}
    if args.name not in models:
        raise LocalAIError(f"Unknown model bundle {args.name!r}; choices: {', '.join(sorted(models))}")
    model = models[args.name]
    if args.model_action == "check":
        if model_complete(model, env):
            console.print(f"[green]Complete[/green] {model.get('label', args.name)}: {model_destination(model, env)}")
            return
        console.print(f"[red]Incomplete[/red] {model.get('label', args.name)}")
        for path in model_missing(model, env):
            console.print(f"  MISSING {path}")
        raise SystemExit(1)
    if args.model_action == "download":
        check_prerequisites()
        ensure_directories(manifest, env)
        if str(model["source"]) == "compose-command":
            build_service(service_dir, manifest, env)
        provision_model(model, service_dir, env)
        return
    raise AssertionError(args.model_action)


def cmd_doctor(args: argparse.Namespace) -> None:
    if args.all:
        repo_root = Path(args.repo_root).resolve()
        service_dirs = sorted(path for path in (repo_root / "services").iterdir() if (path / "service.toml").is_file())
    else:
        service_dirs = [Path(args.service_dir).resolve()]

    problem = False
    for service_dir in service_dirs:
        repo_root = repo_root_from_service(service_dir)
        manifest = load_manifest(service_dir)
        console.rule(service_name(manifest))
        try:
            missing_config = [
                path for path in (repo_root / ".env", service_dir / ".env")
                if not path.is_file()
            ]
            if args.all and missing_config:
                console.print("[yellow]UNCONFIGURED[/yellow] run ./setup.sh in this service to initialize it")
                continue
            require_config(repo_root, service_dir)
            env = effective_env(repo_root, service_dir, manifest)
            validate_effective_env(env, manifest)
            print_plan(service_dir, manifest, env, selected_model_names(manifest, env, []))
            for row in directory_rows(manifest, env):
                if Path(row["path"]).is_dir():
                    console.print(f"[green]OK[/green]      {row['path']}")
                else:
                    console.print(f"[yellow]MISSING[/yellow] {row['path']}")
            for model in manifest.get("models", []):
                if model_complete(model, env):
                    console.print(f"[green]OK[/green]      model {model['name']}")
                elif bool(model.get("required", False)):
                    console.print(f"[red]MISSING[/red] model {model['name']}")
                    problem = True
                else:
                    console.print(f"[dim]optional model not installed: {model['name']}[/dim]")
        except LocalAIError as ex:
            console.print(f"[red]ERROR[/red] {ex}")
            problem = True

    for command in ("uv", "docker", "nvidia-smi"):
        if shutil.which(command):
            console.print(f"[green]OK[/green]      command {command}")
        else:
            console.print(f"[red]MISSING[/red] command {command}")
            problem = True
    raise SystemExit(1 if problem else 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup")
    setup.add_argument("--service-dir", required=True)
    setup.add_argument("--edit", action="store_true", help="edit both shared and service configuration")
    setup.add_argument("--edit-root", action="store_true")
    setup.add_argument("--edit-service", action="store_true")
    setup.add_argument("--accept-defaults", action="store_true", help="install staged defaults without opening an editor")
    setup.add_argument("--with-model", action="append", default=[], help="provision an optional model bundle by manifest name")
    setup.add_argument("--plan", action="store_true", help="show the resolved plan without creating directories/building/downloading")
    setup.add_argument("--no-build", action="store_true", help=argparse.SUPPRESS)
    setup.add_argument("--no-download", action="store_true", help=argparse.SUPPRESS)
    setup.set_defaults(func=cmd_setup)

    start = subparsers.add_parser("start")
    start.add_argument("--service-dir", required=True)
    start.set_defaults(func=cmd_start)

    show = subparsers.add_parser("show")
    show.add_argument("--service-dir", required=True)
    show.set_defaults(func=cmd_show)

    model = subparsers.add_parser("model")
    model.add_argument("model_action", choices=["check", "download"])
    model.add_argument("name")
    model.add_argument("--service-dir", required=True)
    model.set_defaults(func=cmd_model)

    doctor = subparsers.add_parser("doctor")
    group = doctor.add_mutually_exclusive_group(required=True)
    group.add_argument("--service-dir")
    group.add_argument("--all", action="store_true")
    doctor.add_argument("--repo-root", default=".")
    doctor.set_defaults(func=cmd_doctor)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except LocalAIError as ex:
        err_console.print(f"[bold red]ERROR:[/bold red] {ex}")
        raise SystemExit(1)
    except subprocess.CalledProcessError as ex:
        err_console.print(
            f"[bold red]ERROR:[/bold red] command failed with status {ex.returncode}: {shlex.join(ex.cmd)}"
        )
        raise SystemExit(ex.returncode)


if __name__ == "__main__":
    main()
