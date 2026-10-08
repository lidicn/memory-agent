#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""依赖 CVE 扫描量具的回归锁（补 A5/A7 的「pip-audit 跑不起来 ⇒ CVE 未扫」缺口）。

这一族缺陷的原形是**静默归零**：本机第一版只读 OSV 批量接口的 `matches` 键，
而该接口把每条命中放在 `vulns` 键下——于是连已知有洞的 `requests==2.19.1` 都报 0 命中，
形状仍然合法、退出码仍是 0。所以这里的每一条判红都必须配「该不响」对偶档：
只写「读到 vulns 就算成功」而不写「键名对不上必须作废」的话，一个恒返回空的实现也能绿。
"""

from __future__ import annotations

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "dep_audit_cve", os.path.join(REPO, "scripts", "dep_audit_cve.py"))
da = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(da)


def _write(tmp_path, lines):
    p = tmp_path / "freeze.txt"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


# ── 快照解析 ───────────────────────────────────────────────────────────────
def test_parse_freeze_only_counts_pinned_lines(tmp_path):
    got = da.parse_freeze(_write(tmp_path, [
        "requests==2.19.1", "# comment==1.0", "-e git+https://x#egg=y",
        "PyJWT==2.14.0", "requests==2.19.1", ""]))
    names = [q["package"]["name"] for q in got]
    assert names == ["requests", "PyJWT"], names           # 注释/编辑式安装/重复行都不该进表
    assert got[0]["version"] == "2.19.1"


# ── 接口形状：两个历史键名都要读，读不到就判红 ────────────────────────────
def test_hits_from_reads_both_historical_keys():
    assert da.hits_from({"vulns": [{"id": "GHSA-a"}, {"id": "PYSEC-b"}]}) == ["GHSA-a", "PYSEC-b"]
    assert da.hits_from({"matches": [{"vuln_id": "GHSA-c"}]}) == ["GHSA-c"]


def test_hits_from_empty_dict_is_clean_not_broken():
    """该包无已知漏洞时接口回 `{}` —— 这是合法 0 条，不能和"键名对不上"混成一档。"""
    assert da.hits_from({}) == []


def test_hits_from_unknown_shape_refuses_to_report_zero():
    with pytest.raises(RuntimeError, match="读数作废"):
        da.hits_from({"foo": [{"id": "GHSA-a"}]})


# ── 反例自检：不咬就作废整份读数 ───────────────────────────────────────────
def test_main_aborts_when_canary_does_not_bite(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(da, "post_json", lambda url, payload, timeout:
                        {"results": [{}] if "querybatch" in url else {}})
    rc = da.main(["--freeze", _write(tmp_path, ["requests==2.19.1"]), "--details", "0"])
    out = capsys.readouterr().out
    assert rc == 2, "反例不咬却返回 0 ⇒ 一份 0 命中的假绿报告"
    assert "不咬" in out and "命中漏洞=" not in out


def test_main_proceeds_when_canary_bites(tmp_path, capsys, monkeypatch):
    def fake_post(url, payload, timeout):
        qs = payload.get("queries") or []
        return {"results": [{"vulns": [{"id": "GHSA-x"}]} if q["package"]["name"] == "requests"
                            else {} for q in qs]}
    monkeypatch.setattr(da, "post_json", fake_post)
    monkeypatch.setattr(da, "get_json", lambda url, timeout: {"severity": [], "aliases": []})
    rc = da.main(["--freeze", _write(tmp_path, ["requests==2.19.1", "six==1.16.0"])])
    out = capsys.readouterr().out
    assert rc == 0
    assert "命中漏洞=1 涉及包=1" in out and "已扫 2 个包" in out


def test_no_canary_zero_hits_is_marked_unproven(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(da, "post_json",
                        lambda url, payload, timeout: {"results": [{} for _ in payload["queries"]]})
    rc = da.main(["--freeze", _write(tmp_path, ["six==1.16.0"]), "--no-canary"])
    out = capsys.readouterr().out
    assert rc == 0 and "只能记『未证实』" in out


def test_main_aborts_when_result_count_mismatches(tmp_path, capsys, monkeypatch):
    calls = {"n": 0}

    def fake_post(url, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:                     # 反例档：咬
            return {"results": [{"vulns": [{"id": "GHSA-k"}]}]}
        return {"results": [{"vulns": []}]}      # 2 个包只回 1 条

    monkeypatch.setattr(da, "post_json", fake_post)
    rc = da.main(["--freeze", _write(tmp_path, ["a==1.0", "b==2.0"]), "--details", "0"])
    assert rc == 2 and "逐包对位不可靠" in capsys.readouterr().out


def test_network_error_is_scan_not_attempted_never_no_holes(tmp_path, monkeypatch, capsys):
    """两处出网点（反例自检 / 正式批量）都必须把「没扫成」和「没有洞」分开说。"""
    import urllib.error

    def boom(url, payload, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(da, "post_json", boom)
    rc = da.main(["--freeze", _write(tmp_path, ["requests==2.19.1"])])
    out = capsys.readouterr().out
    assert rc == 2
    assert "没扫成" in out and "不是「没有洞」" in out

    calls = {"n": 0}

    def flaky(url, payload, timeout):
        calls["n"] += 1
        if calls["n"] == 1:                      # 反例咬
            return {"results": [{"vulns": [{"id": "GHSA-k"}]}]}
        raise urllib.error.URLError("mid-scan drop")

    monkeypatch.setattr(da, "post_json", flaky)
    rc = da.main(["--freeze", _write(tmp_path, ["requests==2.19.1"]), "--details", "0"])
    out = capsys.readouterr().out
    assert rc == 2 and "正式扫描" not in out and "没扫成" in out


# ── 详情字段：档位/别名/修复版各判一件事 ──────────────────────────────────
def test_best_severity_prefers_the_worse_of_two_sources():
    v = {"severity": [{"type": "CVSS_V3", "score": "7.5/AV:N"}],
         "database_specific": {"severity": "MODERATE"}}
    assert da.best_severity(v) == "HIGH"          # 数值档优先，不被库里标低的抹平
    assert da.best_severity({"severity": []}) == "UNKNOWN"


def test_best_severity_uses_database_label_when_no_score():
    assert da.best_severity({"database_specific": {"severity": "critical"}}) == "CRITICAL"


def test_cve_aliases_keeps_only_cve_ids():
    v = {"aliases": ["PYSEC-2026-1", "CVE-2026-1234", "GHSA-xxxx"]}
    assert da.cve_aliases(v) == "CVE-2026-1234"
    assert da.cve_aliases({}) == "-"


def test_fixed_versions_matches_package_case_insensitively():
    v = {"affected": [{"package": {"name": "Requests"},
                       "ranges": [{"events": [{"introduced": "0"}, {"fixed": "2.32.4"}]}]}]}
    assert da.fixed_versions(v, "requests") == "2.32.4"


def test_fixed_versions_reports_no_fix_instead_of_empty_string():
    v = {"affected": [{"package": {"name": "chromadb"},
                       "ranges": [{"events": [{"introduced": "0.4.17"},
                                               {"last_affected": "1.5.9"}]}]}]}
    assert da.fixed_versions(v, "chromadb") == "无修复版"
    assert da.fixed_versions({}, "chromadb") == "无修复版"
