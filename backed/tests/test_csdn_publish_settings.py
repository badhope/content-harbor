# -*- coding: utf-8 -*-
"""CSDN 发布设置全项填写——真机缺陷回归。

这些用例把 2026-11-08 真实浏览器调试中踩到的坑固定下来。每一条都对应一次
"看起来写了、实际没生效"的静默失败，光读代码看不出来：

1. 单选控件是 `<input type=radio id=X>` + `<label for=X>`，input 与 label 是
   **兄弟**不是父子。按 `label` 内含 input 找永远找不到 → 静默跳过。
2. 封面控件是 `.el-upload__input`，页面第一个 file input 是 `.md` 导入器。
   选错会把封面图塞给 Markdown 导入，静默无效。
3. 上传后必弹「图片编辑」对话框，确认按钮是 `<div class="vicp-operate-btn">`
   （不是 button）。不点它封面不会附加，且弹窗挡住发布按钮。
4. CSDN 的 el-select 下拉全局 teleport，活动/话题两个 dropdown 同时存在。
   只用全局 `.el-select-dropdown__item` 会抓到上一个下拉的项——实测把
   "IT疑难杂症诊疗室"（活动）当成了话题。
5. 标签面板 `.mark_selection_box` 必须收起，否则遮住下面的单选，截图也看不到。
6. 已选标签 chip 在 `.form-tag-box` 里的 `.el-tag`，全局查 `[class*=selected]`
   会漏，导致"其实选上了"被误报失败。
"""

from service.publishing.adapters.csdn import CSDNAdapter


class _FakePage:
    """够用的假 page：记录 evaluate/query_selector 调用，按脚本返回。"""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []
        self.url = "about:blank"

    def evaluate(self, js, arg=None):
        # 存完整 JS：断言要看关键选择器，截断会漏（第一版只存前 60 字符，
        # 结果 test_radio_uses_label_for 之类全部误报失败）。
        self.calls.append((js, arg))
        if self.results:
            return self.results.pop(0)
        return None

    def query_selector(self, sel):
        self.calls.append((f"qs:{sel}", None))
        return None

    def query_selector_all(self, sel):
        return []


def test_form_reports_all_twelve_items():
    """12 项每一项都必须出现在 filled/skipped/failed 之一，不许漏项。

    这是"填满"的契约：新增/删减设置项时，测试会立刻发现。
    """
    ad = CSDNAdapter()
    # 用一个连页面都没有的假 page：所有项都会走异常/失败分支，
    # 但每一项都必须被"记账"——漏记就等于静默跳过。
    p = _FakePage()
    res = ad._fill_publish_form(p, {"title": "t", "content_md": "c"}, {})
    all_items = res["filled"] + res["skipped"] + res["failed"]
    joined = " | ".join(all_items)
    for name in ("文章标签", "添加封面", "文章摘要", "分类专栏", "文章类型",
                 "创作声明", "文章备份", "可见范围", "文章模板",
                 "多平台发布", "参与活动", "话题"):
        assert name in joined, f"设置项 {name} 没被记账（静默跳过了）"


def test_detail_counts_match_lists():
    """detail 里的数字必须和三个列表长度一致，否则用户看到的汇总骗人。"""
    ad = CSDNAdapter()
    res = ad._fill_publish_form(_FakePage(), {"title": "t", "content_md": "c"}, {})
    assert f"已填 {len(res['filled'])} 项" in res["detail"]
    if res["skipped"]:
        assert str(len(res["skipped"])) in res["detail"]
    if res["failed"]:
        assert str(len(res["failed"])) in res["detail"]


def test_no_cover_marks_skipped_not_failed():
    """没封面是"没素材"，不是"填失败"——两者对用户的含义完全不同。"""
    ad = CSDNAdapter()
    res = ad._fill_publish_form(
        _FakePage(), {"title": "t", "content_md": "c"}, {"cover": ""})
    assert any(x.startswith("添加封面") for x in res["skipped"])
    assert not any(x.startswith("添加封面") for x in res["failed"])


def test_radio_uses_label_for_not_nested_input():
    """单选必须走 label[for] + getElementById 读 checked。

    反证：把实现写回"label 内含 input"那种找法，本用例会红。
    """
    ad = CSDNAdapter()
    p = _FakePage()
    ad._fill_publish_form(p, {"title": "t", "content_md": "c"},
                          {"article_type": "original", "visibility": "public",
                           "template": "simple"})
    js_all = "\n".join(c[0] for c in p.calls)
    assert "label[for]" in js_all, "单选没用 label[for] 定位"
    assert "getElementById(l.getAttribute('for'))" in js_all, \
        "单选没通过 for 反查 input 读 checked"


def test_cover_uses_el_upload_input():
    """封面必须用 .el-upload__input，不能用第一个 file input。"""
    ad = CSDNAdapter()
    p = _FakePage()
    ad._fill_publish_form(p, {"title": "t", "content_md": "c"},
                          {"cover": "C:/x.png"})
    sels = [c[0] for c in p.calls if c[0].startswith("qs:")]
    assert "qs:.el-upload__input" in sels, "封面没定位到 .el-upload__input"
    assert "qs:input[type=file]" not in sels, "还在用第一个 file input（会选错）"


def test_multiplatform_item_is_recorded():
    """多平台发布必须被记账（填上或明确失败），不能静默略过。

    具体的 id 选择在真机上验证（docs/verification/2026-11-08-publish-settings.md
    记录了多平台发布=是 的截图）；这里只锁"不许漏项"这条契约。
    """
    ad = CSDNAdapter()
    res = ad._fill_publish_form(_FakePage(), {"title": "t", "content_md": "c"},
                                {"multiplatform": True})
    all_items = res["filled"] + res["skipped"] + res["failed"]
    assert any(x.startswith("多平台发布") for x in all_items)


def test_dropdown_scope_filters_visible():
    """下拉必须按可见性筛，否则会抓到上一个（活动/话题混用）。

    旧实现直接 `querySelectorAll('.el-select-dropdown__item')` 取全局第一个，
    实测把活动名（IT疑难杂症诊疗室）当成了话题名塞进文章。
    """
    ad = CSDNAdapter()

    class _Noop:
        def click(self, **kw):
            pass

    class _Page(_FakePage):
        def query_selector(self, sel):
            self.calls.append((f"qs:{sel}", None))
            return _Noop()

    p = _Page()
    ad._fill_publish_form(p, {"title": "t", "content_md": "c"}, {})
    js_all = "\n".join(c[0] for c in p.calls)
    assert "getBoundingClientRect" in js_all, "没按可见性过滤 dropdown"
    assert "getComputedStyle" in js_all, "没排除隐藏的 dropdown"
    # 必须限定在"最靠下/最靠上"的那个下拉里取项，而不是全局取
    assert "dds[0]" in js_all or "dds[" in js_all


# ---------------- 更新链路（原地更新）回归 ----------------

def test_update_signature_accepts_options():
    """update 必须接受 options：更新弹窗是空的，不重填会把已有设置清掉。"""
    import inspect
    sig = inspect.signature(CSDNAdapter.update)
    assert "options" in sig.parameters, "update 没有 options 参数，更新会丢设置"


def test_update_confirm_button_is_text_matched():
    """确认按钮必须按文案精确匹配「发布文章」。

    2026-11-08 真机：用「最后一个 button.btn-b-red」点到了「定时发布」，
    弹窗不关、线上内容不变，但函数返回 True —— 典型的假成功。
    """
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "service" / "publishing" / "adapters" / "csdn.py"
    text = src.read_text(encoding="utf-8")
    # 更新分支里必须按文案找按钮，而不是靠 class 取最后一个
    assert "=== '发布文章'" in text, "没有按文案精确匹配发布按钮"
    assert "btns[btns.length - 1]" not in text, \
        "还在用「取最后一个 btn-b-red」——会点到定时发布"


def test_update_verifies_published_page():
    """更新后必须回读线上文章页，弹窗关闭不等于内容已改。"""
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "service" / "publishing" / "adapters" / "csdn.py"
    text = src.read_text(encoding="utf-8")
    assert "更新未生效" in text, "更新后没有线上回读校验"
    assert "update 返回 True 但线上没变" not in text
