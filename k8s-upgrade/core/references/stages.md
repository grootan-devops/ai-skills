# Stages

Each stage is one request. It starts with `workspace.py require` and the kubeconfig export,
and ends with `workspace.py record` and a stop (see SKILL.md, "Running a stage"). `$C` is the
cluster folder, `$RUN` the run folder `check` created (`workspace.py status` shows it), and
`$P` the provider folder, `providers/<provider>/` from `cluster.md`.

Each stage below lists its core steps and says where the provider's steps go. The provider's
steps are in `$P/README.md`, under the same stage name.

## init

Read [workspace](./workspace.md). Ask which provider runs the cluster, then:

```bash
python3 core/scripts/workspace.py init <root> <cluster> --provider <name> [--context <kubectl-context>]
```

It creates the workspace without overwriting anything and records `init`. Tell the user which
input files to fill in themselves (never paste credentials into the conversation), to check
`cluster.md`, and to add anything to `critical-extra.txt`; then run `lint`.

## lint

```bash
python3 core/scripts/workspace.py lint "$C"
```

Offline checks of the workspace and the provider's inputs. It records the result itself. On
`FAIL`, list what to fix; the user fixes it and runs `lint` again.

## check

Read-only. Read [preflight](./preflight.md) (steps 2 to 4), [version path](./version-path.md)
and the provider's `check` steps.

1. Work in `$C/_pending/` until the hop is confirmed.
2. Core preflight: cluster-admin, deprecated API use, `webhook_scan.py`.
3. **Provider steps:** access, its preflight, its current versions and the versions it offers.
4. Version path: the hop, the compatibility table for critical components, urgent changelog
   notes, and the provider's findings. Ask the user to confirm the hop.
5. Rename `_pending/` to `<from>-to-<to>-<YYYYMMDD-HHMM>/`, write `plan.md` with everything
   above, and record:

   ```bash
   python3 core/scripts/workspace.py record "$C" check pass --run "$RUN"
   ```

Any stop condition records `fail` instead, with `--note`.

## prepare

Read [preflight](./preflight.md) (step 1), [baseline](./baseline.md) and the provider's
`prepare` steps.

1. Ask for the snapshot confirmation (the provider says what it covers) and record it in
   `plan.md`. Stop without it.
2. **Provider steps, before the baseline:** for example OS package updates, after the user's
   yes.
3. Baseline into `$RUN/pre/`: `critical.py`, `snapshot.sh`, `db_stats.py`, `pvc_usage.py`.
   Show the critical list and ask whether anything is missing; resolve every database that was
   not measured, or get the user to accept it.
4. **Provider steps, backups.** Record each backup's location in `plan.md`.
5. Gate once with no node, and record `pass` only if it passes:

   ```bash
   python3 core/scripts/gate.py --pre "$RUN/pre" --out "$RUN/gates/00-prepared.txt"
   ```

## upgrade

Read [gates](./gates.md), [drain](./drain.md) and the provider's `upgrade` steps; on any
failure, [recovery](./recovery.md) and the provider's recovery.

1. Re-run the gate with no node. If the cluster changed since `prepare` (a gate failing that
   passed then), stop and ask whether to run `prepare` again.
2. **Provider steps:** the control plane, then the nodes one at a time (or a pool at a time
   with a surge of one), each confirmed by the user, each followed by a gate.
3. Record `upgrade pass` when the control plane and every node run the target, then run the
   `validate` stage in the same request (its own `require`, steps and `record`).

A failure records `upgrade fail` with a note naming the node or pool and the step. The next
`upgrade` request resumes; the provider's steps say how it skips what is already upgraded.

## validate

Read [verify](./verify.md). No provider steps.

1. Gate with `--wait 900`, then the post baseline into `$RUN/post/` and `compare.py`.
2. Show every regression and warning; for each warning, ask the user whether it is expected.
3. Ask, then run `smoke_test.sh`. It creates a namespace with a PVC, a pod writing to it, a
   Service, and an Ingress or HTTPRoute, checks them, and deletes the namespace.
4. Write `report.md` and record `validate pass` only when there is no regression, every
   warning is resolved and the smoke test passed.

`validate` can be run on its own again later; it compares with the same prepared baseline.
