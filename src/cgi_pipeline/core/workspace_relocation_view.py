"""Derived current locations; immutable receipt and report sources stay intact."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse


def local_path(value):
    if value.startswith('file:'):
        value = unquote(urlparse(value).path)
        if re.match(r'^/[A-Za-z]:/', value):
            value = value[1:]
    return Path(value)


def report_projection(run_dir, state):
    from cgi_pipeline.core.workspace_relocation import verify_relocation_commit
    from cgi_pipeline.core.workspace_adoption_evidence import digest_file
    relocation = state['relocation']
    proof = verify_relocation_commit(relocation['maintenance_root'], relocation['task_id'],
        relocation['manifest_relpath'], digest_file(run_dir / relocation['commit_relpath']))
    return lambda value: current_path(value, proof)


def scene_binding(run_dir, dcc):
    """Read the current staged source without rewriting its saved provenance."""
    from cgi_pipeline.core import workspace
    run_dir = Path(run_dir)
    state = workspace._read(run_dir / '.info' / 'run_state.json')
    binding = dict(state.get('scene_hosts', {}).get(dcc) or {})
    if binding.get('source_path') and state.get('relocation'):
        binding['source_path'] = report_projection(run_dir, state)(binding['source_path'])
    return binding


def record_report_view(run_dir, state):
    from cgi_pipeline.core import workspace
    from cgi_pipeline.core.workspace_adoption_evidence import digest_file
    relocation = state['relocation']
    journal_path = Path(relocation['maintenance_root']) / '.info' / f"{relocation['task_id']}_relocation_journal.json"
    journal = workspace._read(journal_path)
    journal['report_view_sha256'] = digest_file(run_dir / 'REPORT.md')
    workspace._write(journal_path, journal)


def report_view_status(proof, journal):
    from cgi_pipeline.core.workspace_adoption_evidence import digest_file
    if not proof.get('original_report_backup'):
        return 'NOT_REQUIRED'
    report = Path(proof['target_root']) / 'REPORT.md'
    return 'CURRENT' if report.exists() and digest_file(report) == journal.get('report_view_sha256') else 'PENDING'


def current_path(value, proof):
    if not isinstance(value, str) or not value:
        return value
    is_uri = value.startswith('file:')
    text = unquote(urlparse(value).path) if is_uri else value
    if is_uri and re.match(r'^/[A-Za-z]:/', text):
        text = text[1:]
    candidate = Path(text)
    old, new = Path(proof['source_root']), Path(proof['target_root'])
    if not candidate.is_absolute() or not candidate.is_relative_to(old):
        return value
    mapped = new / candidate.relative_to(old)
    return mapped.as_uri() if is_uri else str(mapped)


def attach_effective_locations(result, proof):
    from cgi_pipeline.core.workspace_relocation import PATH_FIELDS
    value = dict(result)
    originals = dict(result.get('original_paths') or {})
    rows = {}
    def mapped(item):
        if isinstance(item, dict):
            return {key: mapped(child) for key, child in item.items()}
        if isinstance(item, list):
            return [mapped(child) for child in item]
        current = current_path(item, proof)
        if current != item:
            rows[item] = dict(current=current, exists=local_path(current).exists())
        return current
    for key in PATH_FIELDS:
        if key in value:
            updated = mapped(value[key])
            if updated != value[key]:
                originals[key] = value[key]
                value[key] = updated
    # Preserve raw outputs and receipts. Analysis and clickable delivery links
    # consume these explicitly named projections instead of falsifying history.
    if isinstance(value.get('outputs'), dict):
        value['effective_outputs'] = mapped(value['outputs'])
    artifacts = []
    def receipt_paths(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key == 'artifacts' and isinstance(child, list):
                    for artifact in child:
                        current = mapped(artifact)
                        if isinstance(current, str):
                            artifacts.append(dict(original=artifact, current=current, exists=local_path(current).exists()))
                else:
                    receipt_paths(child)
        elif isinstance(item, list):
            for child in item:
                receipt_paths(child)
    receipt_paths(value.get('receipt', {}))
    value.update(workspace_project=proof['binding']['project'], original_paths=originals,
        relocation_task_id=proof['task_id'], effective_paths={**value.get('effective_paths', {}), **rows},
        effective_artifacts=artifacts)
    report = Path(proof['target_root']) / 'REPORT.md'
    if report.exists():
        value['current_workspace_report_path'] = str(report)
    return value
