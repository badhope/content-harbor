# -*- coding: utf-8 -*-
"""标题回读比对的站点后缀剥离——真机缺陷回归。

2026-11-07 真机回读 CSDN 已发布文章时，平台侧标题与目标标题**肉眼完全一样**，
判定却报"不符"。原因：`<title>` 是 `"{文章标题}_CSDN博客"`，比对直接拿整串去
比目标，永远不相等。这个缺陷只有真的打开一个平台页面才会暴露，单测里造
`title="标题"` 是测不出来的。
"""

import pytest

from service.publishing.adapters.base import title_evidence

TITLE = "AIAgent智能体开发从零到一：2026年程序员完整学习路"


@pytest.mark.parametrize("actual,why", [
    (TITLE, "完全一致"),
    (f"{TITLE}_CSDN博客", "CSDN 的 <title> 后缀"),
    (f"{TITLE} - 知乎", "知乎的「 - 站点」后缀"),
    (f"{TITLE} | 掘金", "掘金的 pipe 后缀"),
    (f"{TITLE} — 博客园", "破折号后缀"),
    (f"{TITLE}_CSDN博客_百度搜索", "多个下划线后缀"),
])
def test_site_suffix_is_stripped(actual, why):
    ok, detail = title_evidence(TITLE, actual)
    assert ok is True, f"{why} 应判一致，实际：{detail}"


def test_real_truncation_still_passes():
    """平台截断（实际是目标的前缀）仍要判通过。"""
    ok, _ = title_evidence(TITLE, TITLE[:20])
    assert ok is True


def test_genuinely_different_title_fails():
    """反证：真的不是同一篇时必须红。剥后缀不能宽到什么都放行。"""
    ok, detail = title_evidence(TITLE, "完全不同的一篇文章标题在这里_CSDN博客")
    assert ok is False
    assert "不符" in detail


def test_empty_actual_fails():
    """页面没渲染出标题：读不到就是读不到，不许当通过。"""
    ok, detail = title_evidence(TITLE, "")
    assert ok is False
    assert "为空" in detail


def test_short_actual_fails():
    """太短的"标题"（比如只回读到站点名）不能靠前缀规则蒙混过关。"""
    ok, _ = title_evidence(TITLE, "博客")
    assert ok is False
