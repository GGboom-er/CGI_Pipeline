"""Thin CGI receipt boundary; ordinary Maya imports use ops directly."""

from cgi_pipeline.capabilities.maya.rig.rig_ops_common import receipt, started


def execute(params, context):
    from .ops import combine_groups, reduce_entries

    mode = params.get("mode", "reduce")
    entries=params.get('entries')
    if params.get('entries_path'):
        if entries: raise ValueError('Supply entries or entries_path, not both')
        import json
        from ..proxy_rig.safety import managed
        entries=json.loads(managed(params['entries_path'],suffix={'.json'}).read_text(encoding='utf8'))

    def run():
        if mode == 'analyze':
            from .numeric import analyze
            return analyze(entries,params.get('analysis_workdir'))
        if mode in ("reduce", "regional", "construct"):
            options = params.get("weight_cleanup")
            if options:
                from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights import dair_engine
                dair_engine.require_deps()
                options = dair_engine.validate_params({**options, "mode": "apply", "skin_clusters": ["__source_planning__"]})
            if mode == 'regional':
                from .mapped import reduce_regions
                return reduce_regions(entries, options, params.get('character',''))
            if mode == 'construct':
                from .construction import construct_groups
                return construct_groups(params.get('groups'), options, character=params.get('character',''))
            return reduce_entries(entries, character=params.get("character", ""),
                                  **({"weight_options": options} if options else {}))
        if mode == "combine":
            return combine_groups(params.get("groups"), character=params.get("character", ""))
        raise ValueError("mode must be reduce, regional, construct or combine")

    return receipt(context, "maya.asset.mesh_reduce", started(), params,
                   "MESH_PROCESSING_FAILED",
                   "Read explicit target preconditions and rollback_verified before retrying.", run)
