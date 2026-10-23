# Maya MCP upstream registry

This directory freezes external Maya MCP sources used to design or migrate CGI
Pipeline capabilities. It is not a second runtime package registry and none of
these repositories is a user-facing MCP endpoint.

## Storage contract

- `registry.yaml` pins every source to a full commit and records its intended
  use as `migrate`, `reimplement`, or `reference`.
- `licenses/` contains the license/notice text fetched at that exact commit for
  sources whose root license was verified.
- Full Git repositories are never committed here. When an implementation is
  approved for migration, copy only the selected upstream files into
  `snapshots/<source_id>/<commit>/` and add a manifest containing original
  paths, Git blob IDs, and the target CGI capability IDs.
- A source with `missing_root_file` remains useful for capability comparison,
  but no code is copied from it into CGI Pipeline.

## Why this is separate from `packages/registry.yaml`

`packages/registry.yaml` registers runnable local tools with workspace paths
and host entrypoints. The repositories here are evidence and optional source
material; registering them as runnable packages would falsely imply deployment.

## Refresh procedure

1. Query the upstream repository's current default-branch commit and license.
2. Review the diff from the pinned commit before changing any lock.
3. Update the full commit, commit date, and license Git blob IDs together.
4. Refresh the local license/notice copy from the pinned commit.
5. Run `pytest tests/test_maya_upstream_registry.py -q`.

CGI remains the sole MCP surface. Adopted operations must still become normal
CGI capability manifests and pass the existing catalog, executor, receipt, and
live-DCC validation chain.
