"""引擎冒烟回归测试：覆盖 ISSUES.md 中的关键修复点。

纯 stdlib unittest。运行：python -m unittest engine.tests.test_smoke -v
每例在临时目录创建独立工作区，避免污染真实书稿。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from engine import cli, common


def run(args: list[str]) -> tuple[int, str]:
    """跑 CLI，返回 (退出码, stdout)。stderr 合并进 stdout 便于断言。
    捕获 argparse 的 SystemExit（如 --version / --help 会 SystemExit(0)）。"""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = cli.main(args)
    except SystemExit as e:
        rc = e.code if isinstance(e.code, int) else 0
    return rc, buf.getvalue()


def mk_book(tmp: Path, name: str, title: str, genre: str, pro: str) -> Path:
    rc, out = run(["init", "-w", str(tmp / name), "-t", title, "-g", genre, "-p", pro])
    assert rc == 0, out
    return tmp / name


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class EngineSmokeTest(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)

    # #1 引擎应能启动（--version / help 不因导入缩进而崩）
    def test_engine_imports_and_version(self) -> None:
        rc, out = run(["--version"])
        self.assertEqual(rc, 0, out)
        self.assertIn("novel-studio", out)
        rc, out = run(["help"])
        self.assertEqual(rc, 0, out)
        self.assertIn("sync", out)

    # #16 init 相对路径自动归位到 workspace/（漏写前缀不建到仓库根）
    def test_init_relative_auto_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            prev = Path.cwd()
            # 无需真正 chdir；这里仅验证 _init_workspace 归一化结果
            book = cli._init_workspace("我的书")
            self.assertEqual(book.parent.name, "workspace", str(book))

    # 全流程：init -> 填 bible -> beats -> raw/final -> review -> proposal -> sync -> check(无 error) -> export
    def test_full_flow(self) -> None:
        book = self.root / "b1"
        rc, out = run(["init", "-w", str(book), "-t", "测", "-g", "武侠", "-p", "顾长空"])
        self.assertEqual(rc, 0, out)
        # 填 bible 槽位
        bib = book / "bible" / "project_bible.md"
        text = bib.read_text(encoding="utf-8")
        bib.write_text(text.replace("{{slot:logline|主角+欲望+障碍，一句话}}", "追查旧案。"), encoding="utf-8")
        # beats
        write(book / "outlines" / "vol_01" / "beats" / "ch_001.md", """---
chapter: ch_001
vol: vol_01
form: 单场景章
pov: 顾长空·贴身第三人称
words: 2200-4500
style_notes: 短促 | 直入冲突 | 弱收
guard_extra:
---

## 拍点

## 任务书

## 目标
S1 夜探山门。

## 必须保留
顾长空不知山下人是谁。

## 本章禁忌
禁"眼底闪过""嘴角勾起"。

## 验收
1. 顾长空夜里上山门（写出时间/地点）。
""")
        write(book / "manuscript" / "vol_01" / "raw" / "ch_001_v1.md",
              "夜。顾长空踏上山门石阶。\n暗处传来一声：\"来者何人？\"\n")
        write(book / "manuscript" / "vol_01" / "final" / "ch_001.md",
              "夜。顾长空踏上山门石阶。\n暗处传来一声：\"来者何人？\"\n")
        write(book / "log" / "review" / "ch_001.md",
              "# ch_001 审校注记\n## 验收\n1. ✓ 顾长空夜里上山门（见正文\"夜。顾长空踏上山门石阶\"）。\n")
        write(book / "state" / "inbox" / "ch_001.json", json.dumps({
            "schema": "novel-studio.state-mutation/v2", "chapter": "ch_001",
            "operation_id": "ch_001.syncer.0830z",
            "current": {"time": "夜", "location": "山门", "present_characters": ["顾长空"]},
            "entities": [{"action": "upsert", "name": "顾长空", "type": "person", "summary": "主角", "aliases": []}],
            "lines": [], "timeline": {"events": [{"time": "夜", "event": "夜探山门"}]},
            "ledger": {"transactions": []}, "synopsis": {"title": "夜", "text": "夜探山门。"},
        }, ensure_ascii=False))
        # sync 正式
        rc, out = run(["sync", "-w", str(book), "ch_001"])
        self.assertEqual(rc, 0, out)
        # 状态已合并，实体已注册
        ent = common.load_json(book / "state" / "entities.json")
        self.assertIn("顾长空", [e["name"] for e in ent["entries"]])
        # sync 后再 check 无 error
        rc, out = run(["check", "-w", str(book)])
        self.assertEqual(rc, 0, out)
        self.assertNotIn("❌", out)
        # 导出成功
        rc, out = run(["export", "-w", str(book)])
        self.assertEqual(rc, 0, out)

    # #2 check 在有定稿章节时不崩（回归：tuple 章号误格式化）
    def test_check_does_not_crash_with_final(self) -> None:
        book = mk_book(self.root, "b2", "二书", "都市", "李默")
        write(book / "manuscript" / "vol_01" / "final" / "ch_001.md", "正文。")
        rc, out = run(["check", "-w", str(book)])
        # 不应有 TypeError 崩溃，应正常返回（有 warning 但 rc0）
        self.assertNotIn("Traceback", out)
        self.assertNotIn("TypeError", out)

    # #3 sync 前置体检：present_characters 引用未登记实体 → 不写状态、不进 processed/、不封存
    def test_sync_prescreening_unregistered_entity(self) -> None:
        book = mk_book(self.root, "b3", "三书", "玄幻", "沈拓")
        write(book / "outlines" / "vol_01" / "beats" / "ch_001.md",
              "---\nchapter: ch_001\nvol: vol_01\nform: 单场景章\npov: 沈拓·贴身第三人称\n"
              "words: 2200-4500\nstyle_notes: 短促 | 直入冲突 | 弱收\nguard_extra:\n---\n\n## 验收\n1. 有出场人物。\n")
        write(book / "manuscript" / "vol_01" / "final" / "ch_001.md", "正文。")
        write(book / "log" / "review" / "ch_001.md",
              "# 注记\n## 验收\n1. ✓ 有出场人物（见正文\"正文\"）。\n")
        # 引用未登记的"张三"（proposal 只 upsert 了沈拓）
        write(book / "state" / "inbox" / "ch_001.json", json.dumps({
            "schema": "novel-studio.state-mutation/v2", "chapter": "ch_001",
            "operation_id": "ch_001.syncer.0830x",
            "current": {"present_characters": ["沈拓", "张三"]},
            "entities": [{"action": "upsert", "name": "沈拓", "type": "person", "summary": "主角", "aliases": []}],
            "lines": [], "timeline": {"events": []}, "ledger": {"transactions": []},
            "synopsis": {"title": "", "text": ""},
        }, ensure_ascii=False))
        rc, out = run(["sync", "-w", str(book), "ch_001"])
        self.assertEqual(rc, 1, out)          # 拒绝
        self.assertIn("引用未登记实体", out)
        # 状态未被污染（present_characters 不含张三）
        cur = common.load_json(book / "state" / "current.json")
        self.assertNotIn("张三", cur["present_characters"])
        # 提案未被归档到 processed/ → 应留在 failed/（可捡回）
        self.assertFalse((book / "state" / "inbox" / "processed" / "ch_001.json").exists())
        fail = book / "state" / "inbox" / "failed" / "ch_001.json"
        self.assertTrue(fail.exists(), "应进 failed/ 可捡回")

    # #4 空提案（六区全空）→ no-op，不推进、不封存快照
    def test_empty_proposal_is_noop(self) -> None:
        book = mk_book(self.root, "b4", "四书", "悬疑", "侦探")
        write(book / "outlines" / "vol_01" / "beats" / "ch_001.md",
              "---\nchapter: ch_001\nvol: vol_01\nform: 单场景章\npov: 侦探·贴身第三人称\n"
              "words: 2200-4500\nstyle_notes: 短促 | 直入冲突 | 弱收\nguard_extra:\n---\n")
        write(book / "manuscript" / "vol_01" / "final" / "ch_001.md", "正文。")
        write(book / "state" / "inbox" / "ch_001.json", json.dumps({
            "schema": "novel-studio.state-mutation/v2", "chapter": "ch_001",
            "operation_id": "ch_001.syncer.0830y",
            "current": {}, "entities": [], "lines": [],
            "timeline": {"events": [], "arcs": []}, "ledger": {"transactions": []},
            "synopsis": {"title": "", "text": ""},
        }, ensure_ascii=False))
        rc, out = run(["sync", "-w", str(book), "ch_001"])
        self.assertEqual(rc, 1, out)          # no-op 拒绝封存
        self.assertIn("未合入任何变更", out)
        # 未生成 ch_001_done 快照
        snaps = snapshot_names(book)
        self.assertFalse(any("ch_001_done" in s for s in snaps), snaps)

    # #9 review_gate 允许"带引文/字段值"的精炼证据（不再用 24 字符硬阈值卡死）
    def test_review_gate_accepts_quoted_evidence(self) -> None:
        book = mk_book(self.root, "b5", "五书", "言情", "女主")
        write(book / "outlines" / "vol_01" / "beats" / "ch_001.md",
              "---\nchapter: ch_001\nvol: vol_01\nform: 对话驱动章\npov: 女主·贴身第三人称\n"
              "words: 2000-3400\nstyle_notes: 绵长 | 直入冲突 | 强钩\nguard_extra:\n---\n\n## 验收\n1. 有对白（带引文）。\n2. 有引用。\n")
        write(book / "manuscript" / "vol_01" / "final" / "ch_001.md", "她说：\"走吧\"。\n")
        # 证据行短但带引号 → 应通过（改用"引文/字段"判据，不再用 24 字符硬阈值卡死）
        write(book / "log" / "review" / "ch_001.md",
              "# 注记\n## 验收\n1. ✓ 有对白（\"走吧\"）。\n2. ✓ 有引用（\"走吧\"）。\n")
        # 直接调 review_gate 断言不拦截
        from engine import checks
        self.assertEqual(checks.review_gate(book, "ch_001"), [])

    # #17 status 无定稿时显示"（未定稿）"而非 ch_000
    def test_status_no_final_shows_unfinalized(self) -> None:
        book = mk_book(self.root, "b6", "六书", "科幻", "船长")
        rc, out = run(["status", "-w", str(book)])
        self.assertEqual(rc, 0, out)
        self.assertIn("未定稿", out)
        self.assertNotIn("ch_000", out)


def snapshot_names(book: Path) -> list[str]:
    root = book / "state" / "snapshots"
    if not root.is_dir():
        return []
    return [d.name for d in root.iterdir() if d.is_dir()]


if __name__ == "__main__":
    unittest.main()
