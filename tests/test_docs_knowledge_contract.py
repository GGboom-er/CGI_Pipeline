# -*- coding: utf-8 -*-
"""验证知识库入口和归档边界，避免恢复上下文时读到过时规范。"""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _check(name, condition, detail=""):
    if condition:
        print(f"[PASS] {name}")
        return True
    print(f"[FAIL] {name}: {detail}")
    return False


def main():
    ok = True
    startup_dir = ROOT / "docs" / "ai_startup"
    memory = startup_dir / "00_STARTUP_PROTOCOL.md"
    foundation = startup_dir / "01_PROJECT_FOUNDATION.md"
    kb = startup_dir / "02_KNOWLEDGE_BASE.md"
    docs_readme = (startup_dir / "03_DOC_INDEX.md").read_text(encoding="utf-8")
    legacy_paths = [
        ROOT / "memory" / "README.md",
        ROOT / "docs" / "PROJECT_FOUNDATION.md",
        ROOT / "docs" / "KNOWLEDGE_BASE.md",
        ROOT / "docs" / "README.md",
    ]
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    archive_readme = (ROOT / "docs" / "archive" / "README.md").read_text(encoding="utf-8")
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    tests_readme = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    lessons = (ROOT / "tasks" / "lessons.md").read_text(encoding="utf-8")
    runtime_contract = (ROOT / "docs" / "architecture" / "pipeline_runtime_contract_v1.md").read_text(encoding="utf-8")
    assembly_doc = (ROOT / "docs" / "architecture" / "compare_and_assembly_pipeline_plan.md").read_text(encoding="utf-8")
    pairing_doc = (ROOT / "docs" / "architecture" / "mesh_pairing_phased_logic.md").read_text(encoding="utf-8")
    skill_standard = (ROOT / "skills" / "build_pipeline_skill" / "SKILL.md").read_text(encoding="utf-8")
    history_docs = [
        ROOT / "docs" / "archive" / "history" / "CGI_Pipeline_v2_Architecture_Specification.md",
        ROOT / "docs" / "archive" / "history" / "architecture.md",
        ROOT / "docs" / "archive" / "history" / "ABC_First_Pipeline_Verification_Guide.md",
    ]
    memory_text = memory.read_text(encoding="utf-8") if memory.exists() else ""
    foundation_text = foundation.read_text(encoding="utf-8") if foundation.exists() else ""
    kb_text = kb.read_text(encoding="utf-8") if kb.exists() else ""
    workflow = json.loads((ROOT / "workflows" / "tex_to_rig_verify_and_sync.json").read_text(encoding="utf-8"))
    workflow_skill_ids = [step["skill_id"] for step in workflow.get("steps", [])]
    skill_docs_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "skills").glob("*/SKILL.md")
    )
    old_pairing_phase = "下一轮" + " P0-C"
    old_receipt_outputs = "receipt." + "outputs"
    old_outputs_result = "outputs." + "result"
    old_convention = "CONVENTION" + ".md"

    ok &= _check("AI 必读包目录存在", startup_dir.exists(), str(startup_dir))
    ok &= _check("AI 启动协议存在", memory.exists(), str(memory))
    ok &= _check("启动协议声明启动读取顺序", "启动读取顺序" in memory_text)
    ok &= _check("启动协议声明问题闭环协议", "问题闭环协议" in memory_text)
    ok &= _check("启动协议声明知识归档归属", "知识归档归属" in memory_text)
    ok &= _check("启动协议禁止单 skill 独占跨 skill 经验", "不允许只在单个 `skills/{skill_id}/SKILL.md` 里记录跨 skill 经验" in memory_text)
    ok &= _check(
        "旧必读入口已删除",
        all(not path.exists() for path in legacy_paths),
        ", ".join(str(path) for path in legacy_paths if path.exists()),
    )
    ok &= _check("项目基石文档存在", foundation.exists(), str(foundation))
    ok &= _check("启动协议指向项目基石", "docs/ai_startup/01_PROJECT_FOUNDATION.md" in memory_text)
    ok &= _check("项目基石声明配置化必读", "配置化必读文档" in foundation_text and "AGENTS.md" in foundation_text)
    ok &= _check(
        "项目基石覆盖八个视角",
        all(term in foundation_text for term in [
            "## 一、产品定位视角",
            "## 二、工作区结构视角",
            "## 三、运行架构视角",
            "## 四、Skill 节点视角",
            "## 五、Workflow 编排视角",
            "## 六、数据契约视角",
            "## 七、配置与安全视角",
            "## 八、入口、验证与沉淀视角",
        ]),
    )
    ok &= _check("项目基石覆盖主 workflow", "tex_to_rig_verify_and_sync" in foundation_text)
    ok &= _check("项目基石覆盖工作流数量", "当前共有 8 条 workflow" in foundation_text)
    ok &= _check("知识库入口存在", kb.exists(), str(kb))
    ok &= _check("docs 索引指向启动协议", "docs/ai_startup/00_STARTUP_PROTOCOL.md" in docs_readme)
    ok &= _check("docs 索引指向项目基石", "docs/ai_startup/01_PROJECT_FOUNDATION.md" in docs_readme)
    ok &= _check("docs 索引指向知识库", "docs/ai_startup/02_KNOWLEDGE_BASE.md" in docs_readme)
    ok &= _check("docs 索引声明开工必读", "docs/ai_startup/" in docs_readme)
    ok &= _check("docs 索引不再写旧形变版本范围", "v007-v009" not in docs_readme)
    ok &= _check("AGENTS 声明固定必读包", "docs/ai_startup/" in agents)
    ok &= _check("AGENTS 声明项目基石", "01_PROJECT_FOUNDATION.md" in agents)
    ok &= _check("AGENTS 声明恢复协议", "02_KNOWLEDGE_BASE.md" in agents)
    ok &= _check("AGENTS 声明开工必读", "每个任务开工前必须主动读取 `docs/ai_startup/`" in agents)
    ok &= _check("AGENTS 主链路包含层级修复", "maya_fix_asset_hierarchy" in agents)
    ok &= _check("AGENTS 不再要求 AI 产出写 runs", "AI 的产出写到 `runs`" not in agents)
    ok &= _check("知识库声明开工必读", "docs/ai_startup/" in kb_text)
    ok &= _check("知识库声明项目基石", "docs/ai_startup/01_PROJECT_FOUNDATION.md" in kb_text)
    ok &= _check("知识库禁止被动提醒才读", "不要等用户被动要求" in kb_text)
    ok &= _check("skill 唯一规范已固定", "skills/build_pipeline_skill/SKILL.md" in kb_text)
    ok &= _check("workflow 只读取 output 规则已固定", "workflow 只读取上游 `output` 字段" in kb_text)
    ok &= _check(
        "知识库覆盖主 workflow 当前节点",
        all(skill_id in kb_text for skill_id in workflow_skill_ids),
        ", ".join(skill_id for skill_id in workflow_skill_ids if skill_id not in kb_text),
    )
    ok &= _check("知识库指向测试门禁", "tests/README.md" in kb_text)
    ok &= _check("知识库锁定后续开发基线", "后续开发基线" in kb_text)
    ok &= _check("docs 索引指向运行时契约", "pipeline_runtime_contract_v1.md" in docs_readme)
    ok &= _check("docs 索引指向对比拼装规范", "compare_and_assembly_pipeline_plan.md" in docs_readme)
    ok &= _check("docs 索引指向 skill 唯一规范", "skills/build_pipeline_skill/SKILL.md" in docs_readme)
    ok &= _check("运行时契约反指知识库", "docs/ai_startup/02_KNOWLEDGE_BASE.md" in runtime_contract)
    ok &= _check("运行时契约反指 skill 规范", "skills/build_pipeline_skill/SKILL.md" in runtime_contract)
    ok &= _check(
        "对比拼装规范覆盖当前主链路",
        all(term in assembly_doc for term in [
            "resolve_asset_files",
            "maya_check_asset_hierarchy",
            "maya_fix_asset_hierarchy",
            "maya_sync_rig_incremental",
            "missing_inputs",
        ]),
    )
    ok &= _check(
        "对比拼装规范记录当前批量验证",
        all(asset in assembly_doc for asset in ["mihouwang", "ciweiguai", "maYouB", "cdfbaixingJ", "cdfbaixingL", "xycrowdbig"]),
    )
    ok &= _check("知识库记录 report_sections 已迁移", "旧 `report_sections` 兼容字段已移除" in kb_text)
    ok &= _check("mesh 配对规范不再保留旧阶段计划", old_pairing_phase not in pairing_doc)
    ok &= _check("mesh 配对规范区分 sync 分组", "pairing_groups" in pairing_doc)
    ok &= _check("skill 规范要求同步知识库", "docs/ai_startup/02_KNOWLEDGE_BASE.md" in skill_standard)
    ok &= _check("README 不维护手抄完整技能清单", "python cli.py list-skills" in root_readme)
    ok &= _check("README 指向固定必读包", "docs/ai_startup" in root_readme)
    ok &= _check("README 覆盖层级修复核心技能", "maya_fix_asset_hierarchy" in root_readme)
    ok &= _check("lessons 声明历史字段不当规范", "早期条目可能包含已废弃字段" in lessons)
    ok &= _check("skill 文档不再写旧 receipt outputs 口径", old_receipt_outputs not in skill_docs_text)
    ok &= _check("skill 文档不再写旧 workflow outputs result 口径", old_outputs_result not in skill_docs_text)
    ok &= _check("skill 文档不再引用旧规范跳转页", old_convention not in skill_docs_text)
    ok &= _check("测试门禁包含知识库测试", "test_docs_knowledge_contract.py" in tests_readme)
    ok &= _check("测试门禁包含项目基石", "项目基石" in tests_readme)
    ok &= _check("测试门禁包含 workflow 输入测试", "test_workflow_input_contract.py" in tests_readme)
    ok &= _check("旧 code_samples 归档目录已移除", not (ROOT / "docs" / "archive" / "code_samples").exists())
    ok &= _check("archive 索引不再暴露 code_samples", "code_samples" not in archive_readme)
    ok &= _check(
        "强表述历史文档有归档提示",
        all("历史归档" in path.read_text(encoding="utf-8") for path in history_docs),
    )

    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
