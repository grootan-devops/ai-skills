# Credential files (kubeadm)

The kubeadm provider reaches every node over SSH. `init` creates these two files in the
cluster folder from this provider's `assets/`.

## Format

Each file holds one Markdown table. Column order does not matter; header names do.

| Column | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Kubernetes node name, exactly as `kubectl get nodes` shows it |
| `host` | yes | Address SSH connects to |
| `port` | no | SSH port, default 22 |
| `user` | yes | SSH user |
| `auth` | yes | `password` or `key` |
| `password` | for `password` | SSH password |
| `ssh_key_path` | for `key` | Private key path; `~` is expanded |
| `sudo_password` | no | Password for `sudo -S`; falls back to `password`; empty with `key` means passwordless sudo |

Write a `|` inside a value as `\|`. Rows of `control-plane.md` are control planes; the first row is the node `kubeadm upgrade
apply` runs on. Rows of `workers.md` are workers, upgraded in the order listed unless the user
asks otherwise.

## Using the credentials

Only `providers/kubeadm/scripts/remote.sh` reads passwords, through `nodes.py --get`. It passes the SSH password
to `sshpass` in the `SSHPASS` environment variable and the sudo password on the first line of
standard input to `sudo -S`. Never put a password on a command line, in `log.md`, in
`plan.md`, or in the conversation. When you record a remote command in `log.md`, record the
`remote.sh` call, not what it expands to.
