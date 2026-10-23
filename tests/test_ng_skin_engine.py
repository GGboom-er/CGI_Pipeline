"""Pure parameter/oracle contracts; these tests do not certify Maya or ng behavior."""
from array import array
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).parents[1] / "src/cgi_pipeline/capabilities/maya/rig/maya_clean_skinweights/ng_engine.py"
SPEC = importlib.util.spec_from_file_location("skin_ng_engine_pure_test", PATH)
engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(engine)


def options(**changes):
    return engine.validate_params({"skin_clusters": ["explicitSkin"], **changes})


def snapshot(rows, locked=(), mode=1):
    return {"name": "explicitSkin", "width": len(rows[0]), "count": len(rows),
            "weights": array("d", [value for row in rows for value in row]),
            "attrs": {"normalizeWeights": mode},
            "influences": [{"locked": index in locked} for index in range(len(rows[0]))]}


def test_module_imports_without_maya_or_ng_dependency():
    assert callable(engine.run)


def test_explicit_defaults():
    assert options() == {"skin_clusters": ["explicitSkin"], "mode": "inspect",
                         "threshold": .001, "max_influences": 12,
                         "remove_unused_influences": True}


@pytest.mark.parametrize("params", [None, {}, {"skin_clusters": []},
                                    {"skin_clusters": "skin"}, {"skin_clusters": [""]},
                                    {"skin_clusters": ["skin", "skin"]}])
def test_missing_or_ambiguous_targets_fail_before_host_import(params):
    with pytest.raises(ValueError):
        engine.run(params)


@pytest.mark.parametrize("changes", [
    {"threshold": True}, {"threshold": -1}, {"threshold": 1},
    {"threshold": float("nan")}, {"threshold": float("inf")},
    {"threshold": "0.001"}, {"threshold": 10 ** 1000},
    {"max_influences": True}, {"max_influences": 0}, {"max_influences": 2.0},
    {"remove_unused_influences": 1}, {"mode": "selection"},
])
def test_parameter_errors_are_value_errors(changes):
    with pytest.raises(ValueError):
        options(**changes)


def test_threshold_is_inclusive_and_retained_ratios_are_preserved():
    expected = engine.expected_row([.001, .005, .094, .9], .005, 12)
    assert expected == pytest.approx([0, 0, .094 / .994, .9 / .994])


def test_post_mode_uses_effective_weights_and_allows_normalized_output():
    assert engine.expected_row([1, 2, 3, 4], .1, 2, 2) == pytest.approx([0, 0, 3 / 7, 4 / 7])


def test_threshold_applies_to_original_effective_weights_before_cap():
    assert engine.expected_row([.14, .14, .72], .15, 2) == [0, 0, 1]


def test_physical_influence_count_can_exceed_per_vertex_cap():
    data = snapshot([[0, 0, .3, .7], [.4, .6, 0, 0]])
    engine._plan(data, options(max_influences=2, threshold=0))
    assert data["changed_vertices"] == 0
    assert data["before_cap"] == 2


@pytest.mark.parametrize("values,mode,message", [
    ([0, 0], 1, "zero"), ([-.1, 1.1], 1, "negative"),
    ([float("nan"), 1], 1, "nonfinite"), ([float("inf"), 1], 1, "nonfinite"),
    ([1e308, 1e308], 1, "nonfinite"), ([1, 2], 0, "nonunit"),
    ([.5, .5], 3, "mode"),
])
def test_unsupported_rows_fail_explicitly(values, mode, message):
    with pytest.raises(ValueError, match=message):
        engine.expected_row(values, 0, 12, mode)


def test_filter_empty_row_is_rejected_instead_of_fallback_assignment():
    with pytest.raises(ValueError, match="empty a row"):
        engine.expected_row([.1, .2, .3, .4], .6, 12)


def test_nonzero_lock_is_rejected_when_ng_would_modify_skin():
    data = snapshot([[.1, .2, .3, .4]], locked=[3])
    with pytest.raises(ValueError, match="locked influences"):
        engine._plan(data, options(max_influences=2))


def test_locked_unchanged_skin_and_zero_locked_binding_do_not_conflict():
    unchanged = snapshot([[0, .3, .7]], locked=[2])
    engine._plan(unchanged, options(max_influences=2))
    assert unchanged["changed_vertices"] == 0
    changing = snapshot([[0, .1, .2, .7]], locked=[0])
    engine._plan(changing, options(max_influences=2))
    assert changing["changed_vertices"] == 1


def test_acceptance_allows_either_equal_weight_cap_tie():
    assert engine.verify_row([.25] * 4, [0, .5, 0, .5], 0, 2) == 0


@pytest.mark.parametrize("before,after,threshold,maximum,message", [
    ([.2, .3, .5], [.2, .3, .5], 0, 2, "cap"),
    ([.2, .3, .5], [0, .3, .5], 0, 2, "normalization"),
    ([.1, .2, .7], [.1, 0, .9], .1, 2, "threshold"),
    ([.1, .2, .7], [0, 0, 1], 0, 2, "discarded"),
    ([.1, .2, .7], [1 / 3, 2 / 3, 0], 0, 2, "discarded"),
    ([.1, .2, .7], [0, .4, .6], 0, 2, "proportions"),
    ([.5, .5], [-.1, 1.1], 0, 2, "invalid"),
])
def test_acceptance_rejects_engine_semantic_failures(before, after, threshold, maximum, message):
    with pytest.raises(RuntimeError, match=message):
        engine.verify_row(before, after, threshold, maximum)


def test_every_positive_weight_counts_even_below_numerical_floor():
    with pytest.raises(RuntimeError, match="cap"):
        engine.verify_row([1e-12, .4, .6 - 1e-12], [1e-12, .4, .6 - 1e-12], 0, 2)


def test_only_ng_documented_numerical_floor_can_explain_extra_loss():
    assert engine.verify_row([1e-10, .2, .8 - 1e-10], [0, .2, .8], 0, 3) < 1e-7
    with pytest.raises(RuntimeError, match="discarded"):
        engine.verify_row([1e-9, .2, .8 - 1e-9], [0, .2, .8], 0, 3)


def test_zero_relationship_detection_is_exact():
    assert engine._zero_columns(snapshot([[0, 1e-20, 1], [0, 0, 1]])) == [0]


class UndoBoundary:
    """Control-flow double only; this does not emulate Maya undo semantics."""

    def __init__(self, matches=True):
        self.calls = []
        self.chunk = "prior-user-action"
        self.matches = matches

    def undoInfo(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("openChunk"):
            self.chunk = kwargs["chunkName"]
        if kwargs.get("query") and kwargs.get("state"):
            return True
        if kwargs.get("query") and kwargs.get("undoName"):
            return self.chunk if self.matches else "prior-user-action"

    def undo(self):
        self.calls.append({"undo": True})

    def ls(self, **_):
        return []


def prepare_failed_transaction(monkeypatch, restoration):
    monkeypatch.setattr(engine, "_node_ids", lambda *_: set())
    monkeypatch.setattr(engine, "_needs_write", lambda *_: True)

    def fail(*_):
        raise RuntimeError("engine command failed")

    monkeypatch.setattr(engine, "_apply_skin", fail)
    outcomes = iter(restoration)

    def restored(*_):
        error = next(outcomes)
        if error:
            raise error

    monkeypatch.setattr(engine, "_restored", restored)


def test_empty_failed_transaction_never_undoes_prior_user_action(monkeypatch):
    host = UndoBoundary()
    prepare_failed_transaction(monkeypatch, [None])
    with pytest.raises(RuntimeError, match="rollback verified"):
        engine._transaction([{}], options(), host, None, None, None, None)
    assert {"closeChunk": True} in host.calls
    assert {"undo": True} not in host.calls


def test_partial_failure_closes_then_undoes_matching_chunk_and_checks_restore(monkeypatch):
    host = UndoBoundary()
    prepare_failed_transaction(monkeypatch, [ValueError("target temporarily invalid"), None])
    with pytest.raises(RuntimeError, match="rollback verified"):
        engine._transaction([{}], options(), host, None, None, None, None)
    assert host.calls.index({"closeChunk": True}) < host.calls.index({"undo": True})


def test_missing_transaction_undo_entry_is_explicit_rollback_failure(monkeypatch):
    host = UndoBoundary(matches=False)
    prepare_failed_transaction(monkeypatch, [RuntimeError("weights changed")])
    with pytest.raises(RuntimeError, match="rollback_failed.*no matching native undo"):
        engine._transaction([{}], options(), host, None, None, None, None)
    assert {"undo": True} not in host.calls


def test_failed_restore_after_undo_cannot_report_success(monkeypatch):
    host = UndoBoundary()
    prepare_failed_transaction(monkeypatch, [RuntimeError("weights changed"), RuntimeError("weights still changed")])
    with pytest.raises(RuntimeError, match="rollback_failed.*weights still changed"):
        engine._transaction([{}], options(), host, None, None, None, None)


def test_apply_noop_does_not_create_empty_undo_chunk():
    host = UndoBoundary()
    data = snapshot([[.3, .7]])
    data.update({"uuid": "skin-id", "shape": "mesh", "before_cap": 2,
                 "changed_vertices": 0, "zero_influences": []})
    data["attrs"].update({"maxInfluences": 12, "maintainMaxInfluences": True, "skinningMethod": 0})
    data["influences"] = [{"name": "j" + str(i), "uuid": "id" + str(i),
                           "logical_index": i, "physical_index": i} for i in range(2)]
    result = engine._transaction([data], options(), host, None, None, None, None)
    assert result[0]["changed"] is False
    assert result[0]["engine_applied"] is False
    assert host.calls == []


def test_selection_restoration_is_inside_transaction_before_close(monkeypatch):
    host = UndoBoundary()
    host.selected = ["original"]
    host.ls = lambda **_: list(host.selected)

    def select(items, **_):
        host.calls.append({"select": items})
        host.selected = list(items)

    host.select = select
    prepare_failed_transaction(monkeypatch, [None])

    def fail_after_selecting(*_):
        host.selected = ["ng-temporary-selection"]
        raise RuntimeError("engine command failed")

    monkeypatch.setattr(engine, "_apply_skin", fail_after_selecting)
    with pytest.raises(RuntimeError, match="rollback verified"):
        engine._transaction([{}], options(), host, None, None, None, None)
    assert host.calls.index({"select": ["original"]}) < host.calls.index({"closeChunk": True})


def test_fresh_inspect_preflight_only_queries_associated_custom_nodes():
    class FreshSkinRead:
        def listConnections(self, *_args, **_kwargs):
            return []

    class CustomNodeRead:
        def __init__(self):
            self.targets = []

        def list_custom_nodes_for_mesh(self, target):
            self.targets.append(target)
            return []

    ng_read = CustomNodeRead()
    data = snapshot([[.3, .7]])
    engine._preflight(data, options(), FreshSkinRead(), ng_read)
    assert ng_read.targets == ["explicitSkin"]
    assert data["changed_vertices"] == 0


def test_existing_ng_data_is_rejected_without_querying_uninitialized_data_node():
    class ExistingLayers:
        def list_custom_nodes_for_mesh(self, target):
            assert target == "explicitSkin"
            return ["existingUserLayerData"]

    with pytest.raises(ValueError, match="existing ng layer data"):
        engine._preflight(snapshot([[.3, .7]]), options(), None, ExistingLayers())


def test_plan_reports_threshold_cap_and_combined_source_effective_loss():
    data = snapshot([[.001, .004, .095, .9], [0, .02, .03, .95]])
    engine._plan(data, options(threshold=.005, max_influences=1))
    loss = data["planned_removed_mass"]
    assert loss["threshold"] == pytest.approx({"max": .005, "total": .005, "mean": .0025})
    assert loss["cap"] == pytest.approx({"max": .095, "total": .145, "mean": .0725})
    assert loss["combined"] == pytest.approx({"max": .1, "total": .15, "mean": .075})


def test_loss_statistics_use_post_mode_effective_weights():
    data = snapshot([[1, 2, 3, 4]], mode=2)
    engine._plan(data, options(threshold=.1, max_influences=2))
    loss = data["planned_removed_mass"]
    assert loss["threshold"]["total"] == pytest.approx(.1)
    assert loss["cap"]["total"] == pytest.approx(.2)
    assert loss["combined"]["max"] == pytest.approx(.3)


def test_extra_numerical_floor_loss_excludes_mathematically_required_cap_loss():
    before = [1e-10, 2e-10, 3e-10, 1 - 6e-10]
    after = [0, 0, 0, 1]
    assert engine.extra_floor_mass(before, after, 0, 1) == 0
    assert engine.extra_floor_mass(before, after, 0, 2) == pytest.approx(3e-10)
    assert engine.extra_floor_mass(before, after, 0, 4) == pytest.approx(6e-10)
    assert engine.extra_floor_mass(before, after, 2e-10, 4) == pytest.approx(3e-10)
