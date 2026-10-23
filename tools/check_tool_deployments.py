"""Inspect deployed tools from owner cards without importing any DCC or tool code."""
import argparse
import json
from pathlib import Path
import re

import yaml

CGI = Path(__file__).resolve().parents[1]
NOTES = CGI.parents[2]
TOKEN = re.compile(r"^[a-z][a-z0-9_]*$")


def read_card(path):
    text = path.read_text(encoding="utf-8-sig")
    parts = text.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError("Missing tool frontmatter: {}".format(path))
    data = yaml.safe_load(parts[1])
    if not isinstance(data, dict):
        raise ValueError("Invalid tool frontmatter: {}".format(path))
    return data


def resolve_inside(root, relative):
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError("A non-empty relative path is required")
    if Path(relative).is_absolute():
        raise ValueError("Owner paths must be relative")
    path = (root / relative).resolve()
    path.relative_to(root.resolve())
    return path


def validate_classification(value):
    if not isinstance(value, dict):
        raise ValueError("Missing classification")
    for field in ("hosts", "stages", "functions", "kinds"):
        values = value.get(field)
        if not isinstance(values, list) or not values:
            raise ValueError("classification.{} must be a non-empty list".format(field))
        if any(not isinstance(v, str) or not TOKEN.fullmatch(v) for v in values):
            raise ValueError("Invalid classification.{} token".format(field))
        if len(values) != len(set(values)):
            raise ValueError("Duplicate classification.{} token".format(field))
    if not isinstance(value.get("module"), str) or not TOKEN.fullmatch(value["module"]):
        raise ValueError("Invalid classification.module")
    return value


def inspect_packages(packages, notes=NOTES, registered=(), apis=(), host="maya"):
    """Return per-tool findings; malformed entries cannot be silently listed as ready."""
    result, seen = [], set()
    registry = {p["package_id"]: p for p in registered}
    for package in packages:
        row = {"id": package.get("id"), "status": "PASS", "issues": []}
        try:
            key = package["id"]
            if key in seen:
                raise ValueError("Duplicate deployment id: {}".format(key))
            seen.add(key)
            card_path = resolve_inside(notes, package["tool_card"])
            card = read_card(card_path)
            if card.get("package_id") != key:
                raise ValueError("Deployment id differs from owner card")
            other = registry.get(key)
            facets = dict(card.get("classification") or {})
            if other:
                if "hosts" in facets or "stages" in facets:
                    raise ValueError("hosts/stages belong to CGI package registry; remove duplicate card fields")
                facets.update(hosts=other["hosts"], stages=other["stages"])
            facets = validate_classification(facets)
            if host not in facets["hosts"]:
                raise ValueError("Deployment host does not match tool owner: " + host)
            source = resolve_inside(notes, card["source"])
            deployment = resolve_inside(notes, package["root"])
            if not source.is_dir() or not deployment.is_dir():
                raise ValueError("Source or deployment directory does not exist")
            # A library's Python search root can be its parent; never allow unrelated roots.
            if source != deployment and source not in deployment.parents and deployment not in source.parents:
                raise ValueError("Deployment root and owner source are unrelated")
            if other:
                if Path(other["source"]["path"]).resolve() != deployment:
                    raise ValueError("CGI package source differs from deployment")
                if other["entrypoints"].get(host) != package.get("entrypoint"):
                    raise ValueError("CGI and deployment entrypoints differ")
                if other.get("versions", {}).get(host) != package.get("versions", package.get("maya_versions")):
                    raise ValueError("CGI and deployment versions differ")
            capabilities = card.get("api_capabilities", [])
            if not isinstance(capabilities, list) or any(not isinstance(c, str) for c in capabilities):
                raise ValueError("api_capabilities must be a list of capability names")
            linked = [a for a in apis if a["executor"] == host and a["capability"] in capabilities]
            missing = set(capabilities) - {a["capability"] for a in linked}
            if missing:
                raise ValueError("Unregistered API capabilities: {}".format(sorted(missing)))
            row.update(classification=facets, tool_card=str(card_path), source=str(source),
                       entrypoint=package.get("entrypoint"), plugin=package.get("plugin"),
                       host=host, versions=package.get("versions", package.get("maya_versions", [])),
                       api_ids=[a["api_id"] for a in linked],
                       hosts_stages_owner="cgi_package_registry" if other else "tool_card",
                       cgi_package="matched" if other else "not_registered")
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
            row.update(status="FAIL", issues=[str(error)])
        result.append(row)
    return result


def select_rows(rows, **filters):
    fields = {"host": "hosts", "stage": "stages", "module": "module",
              "function": "functions", "kind": "kinds"}
    return [r for r in rows if all(
        not value or (value == r.get("classification", {}).get(fields[key]) if key == "module"
                      else value in r.get("classification", {}).get(fields[key], []))
        for key, value in filters.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-host", choices=("maya", "blender", "ue"))
    for name in ("host", "stage", "module", "function", "kind"):
        parser.add_argument("--" + name)
    args = parser.parse_args(argv)
    registry = yaml.safe_load((CGI / "packages/registry.yaml").read_text(encoding="utf-8"))
    apis = json.loads((CGI / "src/cgi_pipeline/data/capabilities.json").read_text(encoding="utf-8"))
    rows = []
    for host, relative in registry["deployments"].items():
        if args.deployment_host and host != args.deployment_host:
            continue
        path = resolve_inside(NOTES, relative)
        packages = json.loads(path.read_text(encoding="utf-8"))["packages"]
        rows.extend(inspect_packages(packages, registered=registry["packages"], apis=apis, host=host))
    failures = [r for r in rows if r["status"] == "FAIL"]
    filters = {k: v for k, v in vars(args).items() if k != "deployment_host"}
    report = {"status": "FAIL" if failures else "PASS", "scope": args.deployment_host or "all_deployments",
              "checked": len(rows), "failures": failures, "tools": select_rows(rows, **filters)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
