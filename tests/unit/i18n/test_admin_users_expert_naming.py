"""Admin Users UI must call agents 「角色」 / Experts (supersedes 「专家」, issue #155)."""

from __future__ import annotations

import json
from pathlib import Path


def test_admin_users_agent_column_uses_expert_terminology() -> None:
    repo = Path(__file__).resolve().parents[3]
    zh = json.loads((repo / "dashboard/src/locales/zh.json").read_text(encoding="utf-8"))
    en = json.loads((repo / "dashboard/src/locales/en.json").read_text(encoding="utf-8"))
    assert zh["adminUsers"]["colAgents"] == "角色"
    assert zh["adminUsers"]["agentsDrawerTitle"] == "{{username}} 的角色"
    assert zh["adminUsers"]["noAgents"] == "该用户暂无角色"
    assert en["adminUsers"]["colAgents"] == "Experts"
    assert en["adminUsers"]["agentsDrawerTitle"] == "{{username}}'s experts"
    assert en["adminUsers"]["noAgents"] == "This user has no experts yet"
