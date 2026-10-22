# Operations and recovery

This companion is an opt-in Python 3.10+ service. Its queue and knowledge databases live on one host's local disk. Existing Odysseus and OpenClaw installations retain their own configuration, memory, and startup mechanisms.

The recommended first operating arrangement is a small control host for the hub, a separate GPU worker, and a client/recovery machine. A worker may be unavailable without preventing the hub from retaining queued work. No Docker, WSL2, paid endpoint, or model download is required for the synthetic demonstration.

## What has to be established on each machine

Record the exact Python executable, configuration path, state directory, process owner, startup mechanism, disk free space, and approved project roots. Identify the existing OpenClaw/Ollama startup owners before adding anything. Keep configuration and exported credentials in a private directory outside the Git checkout.

Run the local diagnostic command from this directory:

```text
python lab.py doctor --config /absolute/private/hub/config.json
```

`doctor` checks Python, SQLite FTS5, disk availability, optional command presence (`git`, `ollama`, `tailscale`, `openclaw`), and local port occupancy. It does not run those programs or contact a service. Port occupancy is a clue, not a health check: an occupied port could be the expected running service or an unrelated process. A missing optional executable does not prevent the queue and lexical knowledge store from working. Constrained environments may report port checks as unknown.

Use the authenticated live status command after starting the hub:

```text
python lab.py status --config /absolute/private/hub/config.json
```

The hub listens on loopback by default. Remote clients need the private HTTPS or SSH-forwarding route described in the main README. This package does not create a public listener or change network configuration.

## Native launchers

### Windows foreground and optional logon task

Supply the actual interpreter path, including a virtual-environment interpreter when applicable:

```powershell
.\scripts\start-homelab.ps1 `
  -ConfigPath "$env:LOCALAPPDATA\OdysseusHomelab\hub\config.json" `
  -PythonPath 'C:\Path\To\Python\python.exe' `
  -Role hub
```

For a worker, use `-Role worker` and the exported `worker.json`. The foreground launcher runs exactly the corresponding `python lab.py serve --config ...` or `python lab.py worker --config ...` command. It does not install anything.

After a successful foreground check, the explicit opt-in switch creates a **new current-user logon task**:

```powershell
.\scripts\start-homelab.ps1 `
  -ConfigPath "$env:LOCALAPPDATA\OdysseusHomelab\hub\config.json" `
  -PythonPath 'C:\Path\To\Python\python.exe' `
  -Role hub -InstallStartup -TaskName 'OdysseusHomelab-hub'
```

The task executes the Python supervisor as the current interactive user with limited privileges. It starts on that user's next login, allows one instance, and removes the default daemon lifetime limit. The supervisor retries a failed child up to three times at one-minute intervals per task invocation; a clean exit stops it. Exhausting that budget requires operator inspection and an explicit task start. Task Scheduler's own retry setting is disabled to avoid multiplying the budget. It is allowed to continue on battery; the machine's sleep and power policy still applies. An existing task with the same name is rejected rather than replaced. The installer does not start a second foreground process.

The supervisor records child output, process IDs, exits and retries in `config.supervisor.log` (hub) or `worker.supervisor.log` beside the selected configuration. Logs rotate at 1 MiB with two backups. A Windows job object terminates the child if its supervisor is stopped or killed. A startup handshake prevents the child from running before that protection is installed. This covers process exit, not a hung process or a sleeping/offline machine. An actual login and sleep/wake drill remains required for host acceptance. See Microsoft's [job object lifecycle](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

Inspect the task in Task Scheduler, including Last Run Result. To run it immediately after closing a foreground test, use `Start-ScheduledTask -TaskName 'OdysseusHomelab-hub'`. To remove it intentionally, first stop it and then use Task Scheduler or `Unregister-ScheduledTask` for that exact name. Follow the machine's existing script execution policy; the equivalent Python command can be used directly when local scripts are blocked.

Microsoft documents a default task limit of three days and an unlimited `PT0S` setting. The supplied daemon task explicitly removes the limit. A future scheduled backup job should instead have a finite execution deadline. Sources: [task settings](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset) and [execution-time limit](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-executiontimelimit-settingstype-element).

### macOS foreground and optional user LaunchAgent

```sh
sh scripts/start-homelab.sh \
  --config "$HOME/.odysseus-homelab/hub/config.json" \
  --python /absolute/path/to/python3 \
  --role hub
```

After verifying foreground behavior, add `--install-startup` to create and load a new user LaunchAgent. `--label com.odysseus.homelab.hub` can set its label explicitly. The script refuses an existing plist and does not unload or replace a running job. The LaunchAgent starts at login, restarts unsuccessful exits with a 60-second throttle, and uses an argument array rather than a shell command string. It retains the interpreter selected by the operator, including a virtual environment.

Inspect it with:

```sh
launchctl print "gui/$(id -u)/com.odysseus.homelab.hub"
```

The plist is placed in the current user's `Library/LaunchAgents` directory. Use a distinct label for a worker. Foreground mode also works on POSIX systems; the optional startup installer is restricted to macOS. These starter recipes do not install log aggregation or automatic backup schedules. Use a foreground launch when diagnosing application startup errors, and record the launcher's exit status and authenticated service status.

### Session continuity is different from unattended reboot

| Mechanism | Starts automatically | Continues without a user login after reboot? |
| --- | --- | --- |
| Foreground command | When the operator runs it | No |
| Supplied Windows logon task | At the selected user's login | No |
| Supplied macOS LaunchAgent | At that user's GUI login | No |
| Separately configured service/boot task | Depends on its account, credentials, storage unlock, and platform setup | Must be verified on the host |

The starter scripts deliberately provide a user-session installation. A control host that must recover before login needs a separately reviewed service or startup-task setup with the appropriate account and filesystem access. A sleeping machine is unavailable regardless of its launcher.

On Windows, Tailscale unattended mode is a separate prerequisite for private connectivity after logout/reboot. Verify its current configuration rather than assuming it is enabled. Its official acceptance check is to restart without logging in and test reachability from another tailnet device. [Tailscale unattended mode](https://tailscale.com/docs/how-to/run-unattended). Existing Tailscale Serve background configuration can resume after reboot, but the application still needs its own startup owner. [Serve restart behavior](https://tailscale.com/docs/reference/tailscale-cli/serve).

## Snapshot coverage and privacy

The snapshot command creates a new directory beside a temporary staging directory, verifies it, then publishes it by rename. Its destination parent must already exist. Existing destinations, overlapping source/destination paths, symbolic links, and Windows reparse points are rejected. Use canonical paths when a platform provides aliases such as macOS `/var`.

Only these recognized files are eligible:

| State file | Included? | Recovery handling |
| --- | --- | --- |
| `queue.sqlite3` | Yes, when present | SQLite Online Backup API; running leases invalidated on restore |
| `knowledge.sqlite3` | Yes, when present | SQLite Online Backup API; indexed content and authored memory retained |
| `config.json` | Yes, when present | Restored roots/readers disarmed; state path rebound |
| `secrets/*.token` | Yes, safe regular filenames only | Private snapshot includes values; active credentials rotated on restore |
| `workers/*.json` | Yes | Configuration reference only; re-export after recovery |
| `workers/<node>/worker.json` and `config.json` | Yes | Configuration reference only; re-export after recovery |
| Original project source files | No | Back up the approved source folders separately |
| Artifacts, logs, model weights, arbitrary sibling files | No | Separate explicit backup and retention policy |
| Existing Odysseus/OpenClaw state | No | Use their own state inventory and backup procedures |

The knowledge database contains indexed source text; it is sensitive even when no `.token` file is present. Worker exports can contain adjacent `node.token` files that are not included by the worker-configuration allowlist. The hub's authoritative node credentials are included from `secrets`, and exports must be regenerated after restore. Hub credential references outside `state/secrets` are rejected by snapshot creation rather than silently producing an incomplete credential backup.

Each database is consistent independently, including committed data in SQLite's WAL. The two databases and configuration are not one cross-file transaction. Pause the hub, local ingestion commands, and configuration edits when a common point in time is required. The code uses SQLite's backup API rather than copying a live database file. [SQLite backup documentation](https://sqlite.org/backup.html).

Snapshots are **unencrypted private exports**. The manifest records this and states whether token files are present; it never records token values. POSIX directories/files receive owner-only modes. Python's Windows mode bits do not establish an owner-only NTFS ACL: verify inherited ACLs on the chosen state and backup directories, and restrict access using the host's normal account management. Keep snapshots out of public Git, shared exchange directories, and chat uploads.

SHA-256 file hashes and SQLite integrity checks detect damage and changed files. The manifest is unsigned and does not establish authenticity against someone able to replace both a file and its manifest. Retain snapshots in access-controlled, encrypted, versioned backup storage.

## Snapshot and recovery drill

Create an empty **parent** backup directory first; the snapshot directory itself must be new:

```text
python lab.py snapshot --config /private/hub/config.json --destination /private/backups/2026-10-22
python lab.py verify-snapshot --snapshot /private/backups/2026-10-22
python lab.py restore --snapshot /private/backups/2026-10-22 --destination /private/recovery/hub
```

Restore accepts a nonexistent or empty destination and does not overwrite a populated directory. It verifies all hashes, file coverage, and SQLite integrity before publication. It also:

1. Invalidates active and replay-fencing lease fields on restored running jobs. Jobs with remaining attempts become queued; exhausted jobs become failed. Attempts and completed results are preserved. Stale worker heartbeat records are cleared.
2. Sets `state_dir` to `.` and rebinds hub credential references to the restored local secret files.
3. Clears `projects` and `readers`, requiring deliberate reapproval of source paths. Indexed data stays in the database but has no approved project root until reapproval.
4. Moves the old reader credentials under `recovery/reader-secrets`, outside the active secret paths, and rotates remaining active credentials. Old worker/client exports do not authenticate to the recovered hub.
5. Writes a private recovery receipt containing the previous project IDs and roots, counts, and follow-up requirements. It does not print credentials. The source snapshot is unchanged.

**Stop the original hub before promoting the restored hub.** Lease invalidation and credential rotation do not create a multi-leader system or prevent two independently running hubs from issuing duplicate real-world work. Start the recovery instance only after deciding which copy is authoritative.

Then reapprove each exact source root using `project-add`, re-export workers/readers into new private destinations, reconnect clients to the recovered hub, and verify a known memory record and a known job result. If source files moved to a different machine, restore those original files separately and review the new roots before approval. Re-run ingestion after the source corpus has been restored and checked; recovery of an index alone is not recovery of the corpus.

## Encrypted backup and retention

Restic is a suitable optional native executable for backing up the completed snapshot directories, approved source folders, retained artifacts, and a Git mirror. It supports Windows, macOS, and Linux, and can use an existing SSH/SFTP connection. No Restic server is necessary for a local or SFTP repository. Its scheduler is the OS scheduler; this package does not install a backup schedule. [Restic overview](https://restic.net/), [scheduling](https://restic.readthedocs.io/en/stable/040_backup.html), [repository destinations](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html).

An initial operational target is one successful remote backup each day, with 7 daily, 4 weekly, and 3 monthly snapshots. Adjust that policy to measured data volume and acceptable recovery loss. A second machine protects against one source disk failing; two machines in the same home still share site-level risks. Keep a recoverable copy of the backup password outside the source machine.

Only a Restic backup exit code of **0** counts as complete success; exit code 3 means some input files could not be read. Record the latest successful snapshot ID/time and keep a failed attempt from advancing that timestamp. During an unavailable-destination test, existing snapshots must remain intact. [Backup exit codes](https://restic.readthedocs.io/en/stable/040_backup.html#exit-status-codes).

For the first small backup repository, run `restic check --read-data` and perform a restore to a separate temporary directory. Ordinary `restic check` does not read every data pack. For larger repositories, schedule data subsets and periodic full checks. Run pruning separately from normal backups because it takes an exclusive repository lock. [Data checking](https://restic.readthedocs.io/en/stable/045_working_with_repos.html), [retention and pruning](https://restic.readthedocs.io/en/stable/060_forget.html).

## Pull-only local Git recovery

GitHub remains the canonical remote. An optional mirror supplies an independent local copy without introducing another forge:

```text
python lab.py mirror --source https://github.com/EXAMPLE/PROJECT.git --destination /private/mirrors/project.git
python lab.py mirror --source https://github.com/EXAMPLE/PROJECT.git --destination /private/mirrors/project.git --update
```

The implementation accepts credential-free HTTPS URLs or an explicit absolute local repository path. It uses Git argument arrays with no shell strings, disables interactive credentials, blocks external Git transports, and installs a disabled push URL. It only updates a managed bare mirror with the expected origin; an existing unrelated directory/repository is rejected. A mirror is writable by its owner for fetches and is not an immutable backup.

Back up the mirror with version history: mirror updates can propagate deleted branches and rewritten references. Git LFS objects, submodules, wiki repositories, and hosting metadata need their own handling. A successful `git clone --mirror` does not mean all GitHub data has been preserved. [Git mirror semantics](https://git-scm.com/docs/git-clone), [GitHub repository backups](https://docs.github.com/en/repositories/archiving-a-github-repository/backing-up-a-repository).

Gitea is a later option when local repository browsing, local issues, or local private hosting are needed. Its official documentation provides native Windows binaries and a Windows-service path. The reviewed Forgejo binary guide assumes Linux/systemd. No forge is installed by this companion. [Gitea Windows service](https://docs.gitea.com/installation/windows-service/), [Forgejo binary installation](https://forgejo.org/docs/latest/admin/installation/binary/).

## Artifacts, exchange, and monitoring

Use job-specific staging directories and an explicit results folder for larger outputs. Define size limits, a free-space reserve, cleanup ages, and a retained class before adding bulk generation. A practical starting policy is seven days for failed/temporary outputs and 30 days for ordinary completed outputs, with durable memory and explicitly retained files excluded from automatic deletion. These are operating policy suggestions; this package does not implement a general-purpose artifact server or disk quota manager.

Use existing SSH/SFTP for selected transfers. Add Syncthing only for explicitly named inbox/results folders if recurring exchange justifies it. Keep live SQLite databases, working Git directories, credentials, and broad personal/work folders out of synchronization. Syncthing propagates deletions, and its versioning is off by default and covers remotely received changes rather than local edits. [Syncthing backup caveat](https://docs.syncthing.net/users/faq.html#is-syncthing-my-ideal-backup-application), [versioning](https://docs.syncthing.net/users/versioning.html), [Windows startup](https://docs.syncthing.net/users/autostart.html).

Uptime Kuma is optional when graphical monitor history becomes useful. Its current README supports native Windows with Node.js, Git, and PM2; lack of Windows support is not a reason to defer it. The useful reason to start with the hub status and external checks is to establish a small, recoverable system first. A monitor on the same host cannot independently report that host's complete outage. [Uptime Kuma](https://github.com/louislam/uptime-kuma).

## Host acceptance receipts

The dependency-free automated suite exercises SQLite/WAL backup, manifest tampering, path boundaries, credential rotation, lease recovery, local Git mirrors, and selected launcher argument behavior in the execution environment. It is not evidence that either supplied native startup integration has run on a physical Windows or macOS host.

Record date, host role, application version/commit, result, and evidence for each drill:

| Drill | Acceptance evidence |
| --- | --- |
| Foreground launch | Doctor output, authenticated status, and one synthetic job result |
| User login | New task/LaunchAgent starts exactly one hub/worker and accepts the expected config |
| Reboot before login, if required | Private connectivity and service availability measured without signing in; user launchers alone do not satisfy this |
| Process crash | Expected restart behavior, no duplicate instance, durable queue retained |
| GPU worker offline | Hub reachable; work pending or explicitly failed under the configured retry policy |
| Backup destination offline | Failed attempt visible, successful-backup timestamp unchanged |
| Restore rehearsal | Integrity passes, known job/evidence recovered after reapproval, old credentials rejected |
| Source-corpus recovery | Original approved files separately restored and hashes/paths reviewed |
| Git recovery | Independent clone of restored mirror, integrity check, and known commit present |
| Capacity boundary | Free-space reserve and retention configured for the actual host before larger artifacts are admitted |

Until those host receipts exist, Windows/macOS startup, unattended reboot, Tailscale connectivity, and real-model performance remain unverified.
