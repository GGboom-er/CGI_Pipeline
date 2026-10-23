"""Single-purpose numeric process. Never imports Maya or opens a DCC scene."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback


def _dependencies():
    import numpy as np
    import scipy
    from scipy.optimize import minimize
    fitted = minimize(lambda x: float(np.square(x - [.2, .8]).sum()), [.5, .5],
                      jac=lambda x: 2 * (x - [.2, .8]), method="SLSQP",
                      bounds=[(0., 1.)] * 2,
                      constraints={"type": "eq", "fun": lambda x: x.sum() - 1,
                                   "jac": lambda x: np.ones(2)})
    if not fitted.success or not np.allclose(fitted.x, [.2, .8], atol=1e-7):
        raise RuntimeError("numeric runtime constrained-solve probe failed: " + str(fitted.message))
    return {"numpy": np.__version__, "scipy": scipy.__version__,
            "python_executable": sys.executable, "solver": "scipy.optimize.minimize:SLSQP",
            "probe_solution": fitted.x.tolist()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--request")
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    # Isolated (-I) Python receives only this owner source root, never a host's
    # sys.path or site-packages. cgi_pipeline package __init__ has no DCC imports.
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
    dependencies = _dependencies()
    if args.probe:
        print(json.dumps(dependencies))
        return
    if args.serve:
        for line in sys.stdin:
            try:
                run_request(json.loads(line)["request"], dependencies)
            except Exception as exc:
                traceback.print_exc(file=sys.stderr)
                print(json.dumps({"status": "ERROR", "error": str(exc)}), flush=True)
        return
    run_request(args.request, dependencies)


def run_request(path, dependencies):
    import numpy as np
    from cgi_pipeline.capabilities.maya.rig.maya_clean_skinweights.dair_kernel import optimize_weights, optimize_tiered
    request_path = Path(path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get('operation','').startswith('geometry.'):
        from cgi_pipeline.capabilities.maya.asset.mesh_reduce.analysis_worker import run_request as geometry_request
        return geometry_request(request_path,request,dependencies)
    optimize = optimize_tiered if request.get("tiered") else optimize_weights
    root = request_path.parent
    with np.load(root / "input.npz", allow_pickle=False) as data:
        output, metrics = optimize(data["points"], data["edges"], data["weights"],
            data["masks"], data["moments"], int(data["pose_count"]),
            threshold=request["threshold"], maximum=request["max_influences"],
            neighbor_rings=request["neighbor_rings"], neighbor_max_delta=request["neighbor_max_delta"])
    np.savez(root / "output.npz", weights=output)
    result = {"metrics": metrics, "dependencies": dependencies,
              "request_id": request["request_id"], "runtime_fingerprint": request["runtime_fingerprint"]}
    (root / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status": "SUCCESS", "result": str(root / "result.json")}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
