"""Read installed aivm state; executed with aivm's own Python interpreter."""
import dataclasses
import fcntl
import json
import os
from pathlib import Path
import sys


class WriteBlocked(PermissionError):
    pass


def deny_writes(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        ):
            raise WriteBlocked('Installed aivm attempted a filesystem write; blocked by doctor (open).')
    if event in {'os.mkdir', 'os.chmod', 'os.chown', 'os.remove', 'os.rename',
                 'os.rmdir', 'os.link', 'os.symlink', 'os.truncate', 'subprocess.Popen'}:
        raise WriteBlocked('Installed aivm attempted a mutating operation; blocked by doctor (' + event + ').')


def main():
    sys.addaudithook(deny_writes)
    # aivm path resolution normally ensures XDG directories. Resolve only.
    import aivm.user_paths as paths
    original = paths.user_app_dir
    paths.user_app_dir = lambda *a, **kw: original(*a, **dict(kw, ensure=False))
    from aivm.scoped_store import resolve_store_scope, load_scope_profile, resolve_machine_context
    from aivm.config_store import materialize_vm_cfg
    from aivm.config_store.io import _load_config_document_unlocked
    from aivm.vm.paths import _paths
    config, requested = json.loads(sys.stdin.read())
    scope = resolve_store_scope(config)
    # The public loader creates a lock and recovers pending transactions.
    # Take an existing lock read-only; never create it or recover state here.
    root = scope.store_path.expanduser().resolve()
    lock_path = root.parent / '.aivm-store.lock'
    lock = lock_path.open('rb') if lock_path.exists() else None
    if lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
    if (root.parent / '.aivm-store-transaction').exists():
        raise ValueError('No existing aivm snapshot can be trusted during a pending store transaction; retry after aivm finishes recovery.')
    document = _load_config_document_unlocked(root)
    if not document.sources:
        raise ValueError('No existing aivm configuration found at ' + str(root))
    store = document.store
    profile = load_scope_profile(scope) if scope.is_machine else None
    active = profile.active_vm if profile else store.active_vm
    names = [v.name for v in store.vms]
    selected = requested or (active if active in names else '') or (names[0] if len(names) == 1 else '')
    if not selected or selected not in names:
        raise ValueError('Cannot select default VM; use --vm. Configured VMs: ' + ', '.join(names))
    if scope.is_machine:
        context = resolve_machine_context(store, selected, profile=profile)
        cfg = context.effective_cfg
        user = context.guest_user
    else:
        cfg = materialize_vm_cfg(store, selected)
        user = cfg.vm.user
    runtime = _paths(cfg)
    ip_path = runtime['ip_file']
    result = {
        'store': str(scope.store_path), 'scope': scope.mode,
        'sources': [str(s.path) for s in document.sources],
        'vm': selected, 'guest_user': user,
        'network': dataclasses.asdict(cfg.network),
        'firewall': dataclasses.asdict(cfg.firewall),
        'identity': cfg.paths.ssh_identity_file,
        'known_hosts': str(runtime['known_hosts']),
        'cached_ip': ip_path.read_text().strip() if ip_path.exists() else None,
        'snapshot_lock': 'shared existing lock' if lock else 'no existing lock; snapshot may change during inspection',
    }
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Do not print arbitrary library exceptions/configuration values.
        hint = 'Installed aivm API/config could not be read without writes.'
        if isinstance(exc, WriteBlocked):
            hint = str(exc)
        elif isinstance(exc, OSError):
            hint = 'Cannot read configuration path: ' + str(exc.filename)
        elif isinstance(exc, ValueError) and str(exc).startswith(('No existing aivm', 'Cannot select default VM')):
            hint = str(exc)
        print(json.dumps({'error': type(exc).__name__, 'hint': hint}))
        sys.exit(1)
