"""Resolve project cache outputs through the existing Maya asset collector."""


def resolve(cmds, project, cache_group="", collector=None, config=None):
    if config is None and not cache_group:
        from cgi_pipeline.core.config_loader import load_project_config
        if not project:
            raise ValueError("project is required to resolve the configured rig cache scope")
        config = load_project_config(project)
    candidates = [cache_group] if cache_group else (config or {}).get("stages", {}).get("rig", {}).get("geom_roots", [])
    if not candidates or any(not isinstance(path, str) or not path.startswith("|") for path in candidates):
        raise ValueError("Rig geom_roots must provide absolute Maya DAG candidates")
    if collector is None:
        from cgi_pipeline.hosts.maya import asset_info_collector as collector
    for candidate in candidates:
        matches = cmds.ls(candidate, long=True, type="transform") or []
        if len(matches) > 1:
            raise ValueError("Configured cache root is ambiguous: " + candidate)
        if not matches:
            continue
        root = collector.resolve_cache_group(candidate)
        if root != matches[0]:
            raise ValueError("Shared cache resolver selected an unexpected fallback root")
        transforms = collector.mesh_transforms_under(root)
        outputs = []
        for transform in transforms:
            shape, _orig = collector.get_deform_input(transform)
            visible = cmds.listRelatives(transform, shapes=True, noIntermediate=True,
                                         fullPath=True, type="mesh") or []
            if len(visible) == 1 and shape != visible[0]:
                raise ValueError("Shared output identity differs from the actual output shape")
            outputs.extend(visible)  # Multiple outputs remain visible to the protection checks.
        return root, sorted(set(outputs)), list(candidates)
    raise ValueError("No configured absolute rig cache root exists in the current scene")
