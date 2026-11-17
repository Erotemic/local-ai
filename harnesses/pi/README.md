# Pi host doctor

Run this on the host where aivm manages the VM and infer-stack serves the
gateway. Neither repository needs to be checked out alongside local-ai, and
local-ai does not need to be mounted in the guest. Requires Python 3.10+, the
installed CLIs on PATH, and existing SSH access; the guest needs Python 3.

```bash
./harnesses/pi/doctor.sh
./harnesses/pi/doctor.sh --model qwen3.8-27b-dbirks-hyperqwen-long --json
```

The JSON output is intended for comparing results across hosts. Credential
values are omitted. It includes machine addresses, paths, and model IDs.
Warnings do not cause a nonzero exit; invalid arguments do.

The doctor selects the active VM, or the sole configured VM. It uses an
existing cached VM IP, falling back to the VM's SSH alias. Overrides:

```bash
./harnesses/pi/doctor.sh --vm my-vm --ssh-target agent@10.78.0.123
./harnesses/pi/doctor.sh --aivm-config /path/to/config.toml --skip-guest
```

It reports:

- Installed CLI locations, selected VM, config source files, network and
  configured firewall policy.
- Gateway URL/port from `infer-stack env`, credential availability, and
  authenticated host model discovery.
- The direct guest endpoint and whether its precise firewall exception exists.
- Visible host TCP listeners and authenticated model discovery from the guest.
- Pi settings, extension declarations, selected model, and whether the guest
  credential matches infer-stack's current key.
- Proposed changes to endpoint exceptions, pi preferences, extensions, model,
  and credential synchronization.

No setup command exists yet. The proposed changes are a preview, not an
executable plan. The existing guest model/provider is preserved unless
`--model`/`--provider` is supplied; the preference baseline is dark theme plus
`pi-provider-litellm` and `pi-effort`.

## Read-only behavior and limits

The doctor does not provision software, generate tokens, start VMs, change
firewalls, repair configuration, mount directories, or make inference requests.
Only authenticated model-list GET requests are made; HTTP redirects and proxy
environment variables are disabled for these probes.

`aivm config show --resolved` can persist SSH defaults, and its public store
loader can create a lock file or recover transactions. Instead the doctor uses
the installed library's config reader with filesystem writes and child commands
blocked. It takes a shared lock if the standard store lock already exists, and
refuses pending transactions. If no lock exists, the report flags that the
snapshot might change during inspection. This currently depends on aivm's
`scoped_store`, `config_store.io`, and `vm.paths` APIs; incompatible versions
produce a warning rather than an unverified configuration guess.

The installed infer-stack CLI's `env` queries also run under a write guard.
Existing-directory creation requests are treated as no-ops. Any other blocked
operation invalidates the query even if infer-stack catches the error and
returns a fallback. Both guards disable Python bytecode writes. For unusual
CLI launchers, supply `--aivm-python` or `--infer-stack-python` with the Python
interpreter from that installation. No package installation is attempted.

Guest inspection uses ordinary SSH rather than `aivm ssh`, which prepares a
session. Host keys must already be trusted. The doctor disables host-key writes,
agent forwarding, local commands, SSH proxies, and connection multiplexing.
An SSH configuration requiring a proxy is not supported by this first version.
Credentials are passed to the guest probe over SSH standard input, never as
command arguments. Credential commands in pi settings are not executed; their
comparison is reported as unavailable. Noninteractive guest environment
references may also be unavailable. Extension declarations are inspected;
extension runtime compatibility is not yet verified.

Firewall config is reported as desired state; live nftables rules and libvirt
network XML are not inspected. Docker can publish ports through NAT without a
visible host listener, so `ss` alone does not establish reachability. The guest
probe tests the proposed direct endpoint with infer-stack's key independently
of existing pi settings, allowing network failures and settings drift to be
reported separately. Failed queries leave the change preview incomplete.
