# Pi configuration from the host

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
- The active libvirt network XML and the guest's default IPv4 routes, compared
  against configured network values and the cached VM address.
- Pi settings, extension declarations, selected model, and whether the guest
  credential matches infer-stack's current key.
- Proposed changes to endpoint exceptions, pi preferences, extensions, model,
  and credential synchronization.

The existing guest model/provider is preserved unless `--model`/`--provider`
is supplied. The versioned preference baseline lives in
[`preferences.json`](preferences.json): dark theme, medium startup effort,
automatic compaction, five agent retries with 2-second initial backoff and a
60-second delay cap, and a 15-minute provider timeout. Provider retries remain
zero so pi handles retries at the agent level. Existing compaction token limits
are preserved. The doctor reports differences against the same policy setup
uses, installed extension versions, and the gateway's advertised effort levels.

## Apply pi preferences

Close pi in the guest before setup, so a running session does not overwrite
the updated preferences on exit. Run these commands on the host:

```bash
./harnesses/pi/setup.sh \
  --model qwen3.8-27b-dbirks-hyperqwen-long --effort medium --dry-run

./harnesses/pi/setup.sh \
  --model qwen3.8-27b-dbirks-hyperqwen-long --effort medium

./harnesses/pi/doctor.sh \
  --model qwen3.8-27b-dbirks-hyperqwen-long --effort medium --json
```

Setup uses the existing default/sole VM and selected LiteLLM alias, such as
`litellm-namek`. It sends its code and preferences over SSH, with no checkout or
helper installation in the guest. This first setup step requires an already
direct endpoint reachable from the guest, a credential matching infer-stack,
a discoverable model, and consistent endpoint addressing. It sets the selected pi
provider URL to the verified direct endpoint. If `auth.json` contains a
`LITELLM_BASE_URL` override for that provider, setup updates that URL too, because
it takes precedence over provider settings. Credential values, other providers,
headers, and other auth environment entries are preserved. Setup does not apply
firewall changes or synchronize mismatched credentials. Missing reasoning metadata does not block upgrades,
retry/compaction settings, or saving the requested local effort preference.
The setup result reports `effort.metadata_verified` separately from successful
settings application and retains the doctor's warnings. Pi may hide or clamp
the requested level until the provider discovers the required metadata.

If effort metadata is missing, configure `reasoning_efforts: [low, medium,
xhigh]` on the appropriate HyperQwen infer-stack endpoint and publish that change
through infer-stack, then run `/litellm-refresh` in pi. Model-list presence and
saved local preferences alone do not establish reasoning support.

Setup checks `pi --version` and runs `pi update` if below the recipe-tested
baseline of 1.4.0. `--no-upgrade-pi` makes that condition an error instead. This
baseline is a reproducible recipe choice rather than an upstream minimum-version
claim. It installs `pi-provider-litellm@4.4.2`, `pi-effort@0.1.1`, and
`pi-subagents@0.89.0`. It preserves unrelated packages and package
resource filters while pinning these declarations. Installation happens in
personal scope, never with `-l`. Package commands run outside any project directory.

The subagent policy lives in [`subagent-config.json`](subagent-config.json) and
is merged into `~/.pi/agent/extensions/subagent/config.json` in the VM. Existing
unrelated settings are preserved and changed files get private backups. The
doctor compares this policy and its file revision, just like pi preferences.
Defaults are eager tool exposure, one delegation level, serial parallel-task and
per-run concurrency, one active async run per session, four children per run,
and sixteen children per session. Scheduled runs are disabled. These are defaults
with the plugin's documented override rules, not a global GPU scheduler.
The selected effort also becomes `subagents.defaultThinking`. Built-in roles
inherit the parent's model unless an existing role/model override specifies one;
setup does not replace such overrides or install cloud-provider credentials.

After setup, start a fresh pi session and run `/subagents-doctor`. Try:
`Use scout to identify the files involved in this task; return a short summary.`
Setup never launches a child itself. Web access is not installed; the packaged
research roles that need it will require separate setup. See the upstream
[subagent configuration](https://github.com/nicobailon/pi-subagents/blob/main/docs/configuration.md)
and [model inheritance](https://github.com/nicobailon/pi-subagents/blob/main/docs/models.md)
documentation.

Setup resolves aivm's user-local Node installations under
`~/.local/share/aivm/node/node-v*-linux-*/bin` (and the `AIVM_NODE_ROOT` /
`AIVM_PI_NODE_BIN_DIR` overrides) without requiring a login shell or sourcing
shell startup files. Supported runtimes for the current architecture are ordered
by version. This lets pi's `env node` launcher and npm find the same toolchain
aivm used during provisioning. Setup does not install a missing Node runtime.
Launcher failures report the exact command, exit status, classified loader error,
and discovered Node/npm paths and Node version. Raw process output remains omitted
so errors cannot print credentials from settings or environment variables.

Before any upgrade/install/settings changes, setup saves a private mode-0600
`settings.json.before-local-ai-*` backup in the guest agent directory. Settings
merges preserve credentials, other providers, and unrelated settings. The final
settings write is atomic and mode 0600. Failures keep the backup and report its
path; package caches/installations may have changed and are not rolled back.
Concurrent edits detected before the final write are not overwritten.
When updating an auth URL override, it also saves a private
`auth.json.before-local-ai-*` backup before any upgrade/install/settings changes.
Both file revisions are checked against preflight. Writes are atomic per file;
if a later write fails, earlier writes may have completed and the backups remain.

For a VM previously using socat, run setup on its host, then rerun the doctor.
Pi's reported `baseUrl` should be the bridge endpoint (for example
`http://10.77.0.1:14042`), with both endpoint probes succeeding. The guest's
requests then go directly to the host; setup does not terminate existing socat
processes or modify unrelated tunnel services.

Medium effort is written as pi's startup default and as the selected model's
entry in both pi's `modelThinkingLevels` and pi-effort's remembered `modelEffort`
map. Other models' entries are preserved. This avoids an older per-model choice
overriding the new default. Start a fresh pi session, run `/litellm-refresh`, then
`/effort` to inspect available levels. The doctor verifies stored configuration
and advertised metadata; it does not execute a generation or force compaction.

The setting names and effort behavior are documented in the upstream
[pi settings reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/settings.md)
and [pi-effort documentation](https://pi.dev/packages/pi-effort).

## Read-only behavior and limits

The doctor does not provision software, generate tokens, start VMs, change
firewalls, repair configuration, mount directories, or make inference requests.
Only authenticated model-list and model-info GET requests are made; HTTP redirects and proxy
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

Firewall config is reported as desired state; live nftables rules are not
inspected. Active network XML is queried with `virsh --readonly` against
`qemu:///system`; no sudo or network activation is attempted. If available,
the live gateway is used for the candidate endpoint. Otherwise the configured
gateway remains an unverified candidate. All config/live network drift suppresses
firewall change proposals. Gateway/subnet drift, a cached IP outside the configured
subnet, or a conflicting guest default route also suppresses endpoint changes.
A bridge-name-only mismatch remains a warning: `connection.preview_safe` is false
for firewall changes, while `connection.endpoint_preview_safe` remains true for
pi configuration when addressing agrees. Setup still requires successful
authenticated model discovery from the guest and matching credentials. It does
not edit the host bridge or firewall to resolve the mismatch. Other pi preference
changes can still be previewed. Guest default routes come from `/proc/net/route`.
The currently configured pi endpoint is also checked using its own resolved
guest credential; the host credential is used only for the derived endpoint.
Network errors include a sanitized reason such as `ECONNREFUSED` or timeout.

Docker can publish ports through NAT without a
visible host listener, so `ss` alone does not establish reachability. The guest
probe tests the proposed direct endpoint with infer-stack's key independently
of existing pi settings, allowing network failures and settings drift to be
reported separately. Failed queries leave the change preview incomplete.
