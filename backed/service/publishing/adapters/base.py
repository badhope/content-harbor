# -*- coding: utf-8 -*-
"""平台适配器接口。

四个动作，AI 能管的全靠这四个：
    check_auth   登录了吗
    list         账号里有哪些文章（顺带把 edit_url 抓回来）
    publish      发一篇新的，返回 post_id
    update       原地更新已发布的（打开 edit_url 改内容再保存）

设计原则：
  1. edit_url 一律从列表页抓，不靠拼 URL —— 拼错了就是灾难，抓来的才准
  2. 平台专属的 URL / API / 选择器全部集中在各适配器顶部常量区，改平台只改那一块
  3. 任何一步失败都抛 PlatformError，由上层记进 jobs 表，不影响其他平台
"""

import json
import re
import time

import markdown as _md_lib


def md_to_html(text):
    """Markdown 转 HTML。知乎/B站/头条/开源中国这类富文本编辑器粘贴时用。"""
    if not text:
        return ""
    try:
        return _md_lib.markdown(text, extensions=["fenced_code", "tables", "nl2br"])
    except Exception:
        # 转不了就整段塞进 <pre>，至少内容不丢
        import html as _html
        return f"<pre>{_html.escape(text)}</pre>"


# 通用"往富文本编辑器粘贴 HTML"的 JS。知乎(Draft.js)/头条/开源中国(UEditor) 都吃这套：
# 构造 ClipboardEvent 带 text/html 数据 → 编辑器自己解析富文本，比 setValue 稳。
PASTE_HTML_JS = """([sel, html]) => {
    const editor = document.querySelector(sel);
    if (!editor) return false;
    editor.focus();
    const ev = new ClipboardEvent('paste', {
        bubbles: true, cancelable: true, clipboardData: new DataTransfer(),
    });
    ev.clipboardData.setData('text/html', html);
    editor.dispatchEvent(ev);
    editor.dispatchEvent(new Event('input', {bubbles: true}));
    editor.dispatchEvent(new Event('change', {bubbles: true}));
    return true;
}"""


class PlatformError(Exception):
    pass


def _http_error(url: str, status, res: dict, method: str = "POST") -> PlatformError:
    """构造带限流元数据的错误串。

    任务引擎要靠 `retry-after:` / `x-ratelimit-reset:` 这两个前缀解析平台
    冷却窗口；不把它们带出来，上层只能瞎猜一个默认值。
    """
    parts = [f"{method} {url} 失败({status}): {str(res.get('text', ''))[:200]}"]
    if res.get("retryAfter"):
        parts.append(f"retry-after: {res['retryAfter']}")
    if res.get("rateLimitReset"):
        parts.append(f"x-ratelimit-reset: {res['rateLimitReset']}")
    return PlatformError(" | ".join(parts))


# ---------------- 回读验证原语（verify-after-act）----------------
#
# 2026-09 复盘出的系统缺陷：三个平台都出过「动作没生效但函数返回成功」的假成功
#   - 掘金：点完「确定并发布」只发 article_draft/update，压根不发 article/publish
#   - 知乎：_import_md_file 失败后回退 _inject_editor，两条路都没把正文换掉，
#           点完「保存并发布」照样 return True
#   - 思否：提交按钮 disabled（必填态没满足）时也走到了成功分支
# 根因都一样：判定只看「有没有点下去」，不看「点完之后平台上到底有没有这篇内容」。
# 下面这组原语把「可观测证据」标准化，各适配器复用，不要每个平台各写一套：
#   find_text / verify_text_present  —— 页面级证据（成功文案出现）
#   wait_for_url_change               —— 导航级证据（URL 跳到非编辑器地址）
#   content_evidence / title_evidence —— 内容级证据（回读的正文/标题就是目标）
#   require_content                   —— 内容级证据的一次性包装，正文+标题一起判

# 读任意元素的纯文本。哨兵注释 hub:read-text 供测试的假 page 识别
READ_TEXT_JS = """/* hub:read-text */ (sel) => {
    const el = sel ? document.querySelector(sel) : document.body;
    if (!el) return '';
    return el.innerText || el.textContent || '';
}"""

# 一次性回读「编辑器正文 + 标题框值」。CodeMirror 走 getValue()（innerText 只能拿到
# 可见行，掘金/CSDN 这类长文会读残缺）；其余富文本编辑器走 innerText。
READ_STATE_JS = """/* hub:read-state */ ([editorSel, titleSel]) => {
    const pick = (sel) => {
        const el = sel ? document.querySelector(sel) : null;
        if (!el) return '';
        if (el.CodeMirror && el.CodeMirror.getValue) return el.CodeMirror.getValue();
        const inner = el.querySelector ? el.querySelector('.CodeMirror') : null;
        if (inner && inner.CodeMirror && inner.CodeMirror.getValue)
            return inner.CodeMirror.getValue();
        return el.innerText || el.textContent || '';
    };
    const ti = titleSel ? document.querySelector(titleSel) : null;
    return {
        body: pick(editorSel),
        title: ti ? (ti.value !== undefined && ti.value !== null
                     ? ti.value : (ti.innerText || '')) : '',
    };
}"""

# 比对前先压平：去掉 markdown 记号与所有空白。富文本编辑器会吃掉 **/#/[]()，
# 逐字比正文必然误报；空白也被平台随手重排。中文标点不动——那是最好的指纹。
_FLATTEN_RE = re.compile(r"[\s*_`#>~|\[\]()!\-]+")
# 链接：只留锚文本，URL 丢掉。编辑器把 [a](b) 渲染成 <a href="b">a</a>，
# innerText 里只有 a —— 留着 URL 会把该行撑得比真正的正文还长，于是必然挤进
# 「最长的 3 段」关键片段，再拿一个页面里根本不存在的字符串判缺失（误杀真成功）。
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]*)\]\([^)\n]*\)")
# 图片：锚文本（alt）也丢掉。图片在编辑器里是 <img>，innerText 一个字都不留。
_MD_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
# 尖括号自动链接 <https://x> / <mailto:x>：编辑器渲染后显示的就是里面的原文。
_MD_AUTOLINK_RE = re.compile(r"<((?:https?|mailto):[^>\n]+)>")
# 弯引号 → 直引号。富文本编辑器（知乎 Draft.js 实测 2026-09）在渲染时会把
# ASCII 的 "..." 换成中文弯引号 “...”，同一个字面量两侧就再也比不上。
# 归一化成 ASCII 方向：目标正文是 Markdown 源，弯引号不会出现在源里，
# 折成 ASCII 只会让「编辑器渲染过」这一侧回到源的样子，判定不会放宽。
_SMART_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
})
# 裸 HTML：只列真正常见的标签名（避免把 <T> 这类泛型写法当标签删掉）。
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG_RE = re.compile(
    r"</?(?:br|div|p|span|a|img|strong|em|b|i|u|ul|ol|li|dl|dt|dd|table|thead|"
    r"tbody|tfoot|tr|td|th|h[1-6]|blockquote|pre|code|hr|section|article|aside|"
    r"figure|figcaption|video|audio|iframe|sup|sub|del|ins|font|center|small|label|"
    r"input|button|details|summary|caption|colgroup|col)\b[^>\n]*>",
    re.I)
_ATX_HEADING_RE = re.compile(r"^#{1,6}\s")
_FRAGMENT_MIN_LEN = 6   # 短于这个长度的行（"---"、单个词）没有区分度
# 目标首段之前允许有多少「非目标内容」：首行长度 + 固定小余量。
# 旧实现是 max(40, 首行长度)——短标题（9 字）也自带 40 字免检额度，9~40 字的
# 陈旧前缀能混过去。余量只用来容忍「编辑器顶部夹带一句未保存提示」这类噪声，
# 不再随标题变短而放大。
_HEAD_LEAD_EXTRA = 12
# pos < 0（首段定位不到）时的兜底锚点：取目标正文开头这么多个字去定位。
_HEAD_PROBE_LEN = 20
# 标题截断判定阈值：平台截断只截尾，实际标题必须是目标标题的前缀。
# 旧实现 4 字就放行，且两个方向都放（"目标比实际短"也放），太宽。
_TITLE_PREFIX_MIN = 8


def _flatten(text):
    text = text or ""
    text = _HTML_COMMENT_RE.sub("", text)
    text = _MD_AUTOLINK_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _MD_IMAGE_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = text.translate(_SMART_QUOTES)
    return _FLATTEN_RE.sub("", text)


# 有序列表的序号是渲染出来的，不在文本里。知乎 Draft.js 把 "2. xxx" 渲染成
# <ol><li>，而 <ol> 的 list-style 是 none、序号用 CSS 计数器画成伪元素——
# innerText 一个字都不留（2026-09 真机实测：4 条 <li> 的 innerText 全都从
# "适配器层：…" 开始，没有 "1."）。片段带序号前缀时，命中判定要看去掉序号后。
_OL_MARKER_RE = re.compile(r"^\d{1,3}[.)、]")


def _frag_pos(frag, actual):
    """片段在实际正文里的位置；带有序列表序号前缀时，序号本身找不到就退到
    去掉序号的那截。找不到返回 -1。"""
    pos = actual.find(frag)
    if pos >= 0:
        return pos
    m = _OL_MARKER_RE.match(frag)
    return actual.find(frag[m.end():]) if m else -1


def find_text(body, texts):
    """在页面文本里找第一条命中的候选文案，命中返回该文案，没有返回 ''。

    「成功文案长这样」的判定口径只此一处：verify_text_present 的轮询与各适配器
    的自定义判定都走它，避免同一个平台的同一句提示在两个地方各写一套。
    """
    for t in (texts or []):
        if t and t in (body or ""):
            return t
    return ""


def key_fragments(md, limit=3):
    """从正文里挑出「必须能在页面上看见」的关键片段（默认取最长的 3 段，去重）。

    为什么不做全文逐字比对：富文本编辑器会吃掉 markdown 记号、把 [a](b) 只留锚文本、
    还可能把首行 ATX 标题提到标题框里——逐字比必然误报。取 3 段最有区分度的行做
    包含判定，既能识破「旧正文还在页面上」，又不会被排版差异误杀。
    """
    lines = []
    for idx, raw in enumerate((md or "").splitlines()):
        flat = _flatten(raw)
        if len(flat) >= _FRAGMENT_MIN_LEN:
            lines.append((flat, idx))
    if not lines:
        return []
    out = []
    for flat, _idx in sorted(lines, key=lambda x: (-len(x[0]), x[1])):
        if flat not in out:
            out.append(flat)
        if len(out) >= limit:
            break
    return out


def _required_fragments(md, frags):
    """首行是 ATX 标题时把它从必答集合里剔除——编辑器可能已把它提到标题框。"""
    raw_lines = (md or "").splitlines()
    if not raw_lines or not frags:
        return list(frags)
    head = _flatten(raw_lines[0])
    if _ATX_HEADING_RE.match(raw_lines[0].strip()) and head in frags:
        rest = [f for f in frags if f != head]
        return rest or list(frags)
    return list(frags)


def _doc_order_fragments(md):
    """按文档顺序取「必答片段」。

    key_fragments 是按长度排序的，取的是覆盖面；判定「目标正文是不是就在实际
    正文开头」需要文档顺序，所以这里单独一条：只取第一个够长的正文行（首行
    ATX 标题已按 _required_fragments 的同一理由剔除——它可能被提到标题框）。
    """
    out = []
    for raw in (md or "").splitlines():
        flat = _flatten(raw)
        if len(flat) >= _FRAGMENT_MIN_LEN and flat not in out:
            out.append(flat)
    frags = _required_fragments(md, out)
    return frags


def _target_body_flat(md):
    """目标正文的压平全文（首行是 ATX 标题时剔除它）。

    短文兜底包含判定与 pos<0 兜底锚点都用它：编辑器可能把首行标题提到标题框，
    正文里就没有它了。
    """
    lines = (md or "").splitlines()
    if lines and _ATX_HEADING_RE.match(lines[0].strip()):
        lines = lines[1:]
    return _flatten("\n".join(lines))


def content_evidence(target, actual, min_ratio=0.35):
    """回读比对：目标 Markdown 与页面实际文本讲的是不是同一篇。

    返回 (ok, detail)。ok=False 时 detail 直接进 PlatformError 消息。
    min_ratio 是篇幅下限：只对上了几段开头但正文大半没写进去，同样算没生效。
    """
    t = _flatten(target)
    a = _flatten(actual)
    if not t:
        return True, "目标正文为空，跳过内容比对"
    if not a:
        return False, "页面回读到空内容（编辑器没渲染出来，或页面已跳走）"
    frags = _required_fragments(target, key_fragments(target))
    if frags:
        missing = [f for f in frags if _frag_pos(f, a) < 0]
        if missing:
            return False, (
                "页面内容与目标不符：%d/%d 段关键内容缺失，首段缺失 %r"
                "（目标 %d 字 / 实际 %d 字）"
                % (len(missing), len(frags), missing[0][:24], len(t), len(a))
            )
    else:
        # 目标每一行都短于 _FRAGMENT_MIN_LEN（速查表这类短文）：片段检查与
        # 追加检查双双空转，只剩长度比 = fail-open。退化为「整篇包含」判定，
        # 定位不到就是没写进去，不给「看起来一样」的机会。
        t_body = _target_body_flat(target)
        if t_body and t_body not in a:
            return False, (
                "页面内容与目标不符：短文无法按关键片段比对，退化为整篇包含判定，"
                "但目标正文没在页面里找到（目标 %d 字 / 实际 %d 字）"
                % (len(t), len(a))
            )
    # 关键片段齐全 ≠ 替换成功：旧正文还留在前面、新正文被追加在后面时，
    # 上面的"齐全"与篇幅下限都会通过。所以还要看目标首段落在实际正文的哪。
    head = _doc_order_fragments(target)
    if head:
        pos = _frag_pos(head[0], a)
        first_line = _flatten((target or "").splitlines()[0]) if (target or "").strip() else ""
        lead_budget = len(first_line) + _HEAD_LEAD_EXTRA
        if pos < 0:
            # pos < 0 = 首段压根定位不到（它多半不在「最长的 3 段」里，或编辑器
            # 对这一行做了归一化，比如列表项渲染出项目符号）。旧实现就在这一支
            # 上静默放行 —— 追加检测形同虚设。退化为「目标正文开头的固定长度
            # 必须落在 lead 预算内」；连这段开头都定位不到（编辑器把它改写/
            # 吞掉了）时不判位置失败，交给上面的内容比对说话。
            probe = _target_body_flat(target)[:_HEAD_PROBE_LEN]
            if probe:
                pos = a.find(probe)
        if 0 <= pos and pos > lead_budget:
            return False, (
                "目标正文不在实际正文开头：首个关键片段前面压着 %d 字"
                "（上限 %d 字，实际共 %d 字），像是把新内容追加在旧内容后面，"
                "没有真正替换" % (pos, lead_budget, len(a))
            )
    ratio = len(a) / len(t)
    if ratio < min_ratio:
        return False, (
            "页面内容明显短于目标：实际 %d 字 / 目标 %d 字（占比 %.0f%% < %d%%）"
            % (len(a), len(t), ratio * 100, int(min_ratio * 100))
        )
    return True, (
        "短文整篇包含判定通过，篇幅 %d/%d 字" % (len(a), len(t))
        if not frags else "关键片段齐全，篇幅 %d/%d 字" % (len(a), len(t))
    )


def title_evidence(expected, actual):
    """标题回读比对。忽略纯空白差异（知乎/CSDN 的标题框会吞空格）。"""
    e = _flatten(expected)
    a = _flatten(actual)
    if not e:
        return True, "目标标题为空，跳过标题比对"
    if e == a:
        return True, "标题一致"
    # 只认「实际是目标的前缀」这一个方向：平台截断只截尾不截头。反方向
    # （实际比目标还长）不是截断，是别的东西写进了标题框。
    if len(a) >= _TITLE_PREFIX_MIN and e.startswith(a):
        return True, "标题前缀一致（平台截断）"
    return False, "标题回读为 %r，与目标 %r 不符" % (a[:30], e[:30])


class PlatformAdapter:
    id = ""
    name = ""
    needs_browser = True   # 走标准协议的平台（如博客园 MetaWeblog）设 False，省一个浏览器实例
    login_url = ""
    home_url = ""      # 登录后才能进的页面，用来判断登录态
    list_url = ""      # 内容管理页
    new_url = ""       # 新建文章页

    # ---- 能力矩阵（代码级声明，不是文档幻觉）----
    # 前端发布轨按此置灰不支持的选项，任务引擎按此决定是否降级，
    # `/platforms` 端点原样下发。新增适配器必须显式声明，默认全 False：
    # 默认 True 会让"看起来支持"变成新的假成功来源。
    capabilities = {
        "publish": False,     # 发新文章
        "draft": False,       # 只存草稿
        "update": False,      # 原地更新已发布文章
        "list": False,        # 抓取账号内文章列表
        "tags": False,        # 支持标签/分类
        "cover": False,       # 支持封面图
        "question": False,    # 知乎回答问题这类特有能力
        "hosted_media": False,  # 接受本应用提供的图片 URL（自建站/支持外链的平台）
    }

    # 选择器版本化（第三刀）：每个子类声明它依赖的页面结构版本与关键选择器，
    # 平台改版时 dump-dom 能立刻对上号，而不是报一堆无关错误。
    selector_version = ""    # 例如 "2026-09"（适配/验证时手动更新）
    key_selectors = {}       # 例如 {"editor": ".CodeMirror", "publish_btn": "button:has-text('发布')"}

    # ---------------- 外部成熟工具兜底 -----------------

    @classmethod
    def publish_with_provider(cls, article: dict, options: dict = None) -> dict | None:
        """优先走外部成熟工具（providers/），失败或未安装返回 None 让调用方回退 Playwright。"""
        if not cls.id:
            return None
        try:
            from service.publishing.providers import resolve_provider
            provider = resolve_provider(cls.id)
            if not provider or not provider.is_available() or not provider.check_auth():
                return None
            result = provider.publish(article, options)
            return result.to_dict() if result else None
        except Exception:
            return None

    def sel_fail(self, sel_name):
        """选择器失效的统一报错：带上版本号与 DOM dump 指引，方便一键补适配。"""
        return PlatformError(
            f"[{self.name}] 页面结构失效：关键选择器 '{sel_name}' 未命中"
            f"（适配器版本 {self.selector_version or '未知'}）。"
            f"请先跑一次 dump-dom 采集当前页面结构，更新 core/adapters/{self.id}.py 的 key_selectors。")

    # ---------------- 子类必须实现的四个动作 ----------------

    def check_auth(self, page) -> bool:
        raise NotImplementedError

    def list_articles(self, page, limit=50):
        """返回 [{'post_id','title','url','edit_url','status','stats':{}}]

        未实现的平台统一抛"需实机采集"错误（含 dump-dom 指引），
        而不是静默返回空列表——空列表会让"同步/更新"静默失效。
        """
        raise PlatformError(
            f"[{self.name}] 已发文章列表暂未适配。"
            f"该平台内容管理页 {self.list_url or '未知'} 需要实机登录后 "
            f"用 dump-dom 采集结构，再在 core/adapters/{self.id}.py 补 list_articles；"
            f"或改用该平台官方 API 对接。发布/草稿能力不受影响。")

    def publish(self, page, article: dict, options: dict = None):
        """返回 {'post_id','post_url','edit_url','draft_only'}"""
        raise NotImplementedError

    def update(self, page, pub: dict, article: dict) -> bool:
        """原地更新。pub 里带着 post_id / edit_url"""
        raise NotImplementedError

    # ---------------- 通用工具 ----------------

    def api_get(self, page, url):
        """在页面上下文里发 GET，自动带 cookie —— 比直接 requests 省事也更像真人。"""
        js = """async (u) => {
            const r = await fetch(u, {credentials: 'include'});
            return {status: r.status, text: await r.text(),
                    retryAfter: r.headers.get('retry-after') || '',
                    rateLimitReset: r.headers.get('x-ratelimit-reset') || ''};
        }"""
        res = page.evaluate(js, url)
        if res["status"] != 200:
            raise PlatformError(_http_error(url, res["status"], res, method="GET"))
        try:
            return json.loads(res["text"])
        except Exception:
            raise PlatformError(f"响应不是 JSON: {res['text'][:200]}")

    def api_post(self, page, url, payload, headers=None):
        js = """async ([u, body, hdrs]) => {
            const r = await fetch(u, {
                method: 'POST',
                credentials: 'include',
                headers: Object.assign({'content-type': 'application/json'}, hdrs || {}),
                body: JSON.stringify(body)
            });
            return {status: r.status, text: await r.text(),
                    retryAfter: r.headers.get('retry-after') || '',
                    rateLimitReset: r.headers.get('x-ratelimit-reset') || ''};
        }"""
        res = page.evaluate(js, [url, payload, headers])
        try:
            data = json.loads(res["text"])
        except Exception:
            raise PlatformError(f"响应不是 JSON: {res['text'][:200]}")
        # 2xx 都算成功：思否建草稿返回 201 且带 id（2026-09 实测），
        # 旧判定只认 200，把成功响应误报成失败，草稿白建。err_no 守卫保留。
        status = res["status"]
        if not (200 <= int(status) < 300) or (
                isinstance(data, dict) and data.get("err_no") not in (0, None)):
            raise PlatformError(_http_error(url, status, res))
        return data

    def api_put(self, page, url, payload, headers=None):
        """PUT JSON。更新文章用。"""
        js = """async ([u, body, hdrs]) => {
            const r = await fetch(u, {
                method: 'PUT',
                credentials: 'include',
                headers: Object.assign({'content-type': 'application/json'}, hdrs || {}),
                body: JSON.stringify(body)
            });
            return {status: r.status, text: await r.text()};
        }"""
        res = page.evaluate(js, [url, payload, headers])
        try:
            data = json.loads(res["text"])
        except Exception:
            raise PlatformError(f"响应不是 JSON: {res['text'][:200]}")
        if not (200 <= int(res["status"]) < 300):
            raise PlatformError(f"PUT {url} 失败: {res['text'][:200]}")
        return data

    def api_post_form(self, page, url, fields):
        """multipart/FormData POST。B站专栏草稿用（JSON 会拒）。fields: {str: str}"""
        js = """async ([u, obj]) => {
            const fd = new FormData();
            for (const [k, v] of Object.entries(obj)) fd.append(k, v);
            const r = await fetch(u, {method: 'POST', credentials: 'include', body: fd});
            return {status: r.status, text: await r.text()};
        }"""
        res = page.evaluate(js, [url, {k: str(v) for k, v in fields.items()}])
        try:
            data = json.loads(res["text"])
        except Exception:
            raise PlatformError(f"响应不是 JSON: {res['text'][:200]}")
        if not (200 <= int(res["status"]) < 300) or (isinstance(data, dict) and data.get("code") not in (0, None)):
            raise PlatformError(f"POST {url} 失败: {res['text'][:200]}")
        return data

    def get_cookie(self, page, name):
        return page.evaluate(
            """(n) => {
                const m = document.cookie.split('; ').find(c => c.startsWith(n + '='));
                return m ? m.slice(n.length + 1) : '';
            }""", name)

    def set_editor_content(self, page, content, editor_sel=".CodeMirror"):
        """
        往 Markdown 编辑器里塞内容。三级降级，从最快到最稳：
          1. CodeMirror 实例 setValue（秒级，编辑器能感知）
          2. 剪贴板粘贴
          3. 逐字输入（慢，但一定能触发事件）
        """
        # 1) CodeMirror
        try:
            ok = page.evaluate("""([sel, text]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                const cm = el.CodeMirror || (el.querySelector('.CodeMirror') || {}).CodeMirror;
                if (cm && cm.setValue) { cm.setValue(text); return true; }
                return false;
            }""", [editor_sel, content])
            if ok:
                time.sleep(0.5)
                return "codemirror"
        except Exception:
            pass

        # 2) 剪贴板
        try:
            page.evaluate("(t) => navigator.clipboard.writeText(t)", content)
            page.click(editor_sel, timeout=5000)
            page.keyboard.press("Control+A")
            page.keyboard.press("Control+V")
            time.sleep(0.8)
            return "clipboard"
        except Exception:
            pass

        # 3) 硬输
        page.click(editor_sel, timeout=5000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.insert_text(content)
        return "typing"

    # ---------------- 回读验证（verify-after-act）----------------
    #
    # 约定：任何 publish / update 在返回成功语义之前，都必须用下面这组原语
    # 拿到「平台上确实有这个内容」的可观测证据。拿不到就抛 PlatformError，
    # 让上层记 failed / pending，绝不返回 True 或带 draft_only=False 的字典。

    def verify_or_raise(self, action, ok, detail):
        """把「证不出来」统一变成 PlatformError。ok 为真时原样返回 detail。"""
        if not ok:
            raise PlatformError(f"[{self.name}] {action}未能回读确认：{detail}")
        return detail

    def _safe_eval(self, page, js, arg=None, default=None):
        """回读取值统一入口。页面正在跳转/弹窗时 evaluate 会抛异常（Execution
        context was destroyed 之类），那属于「读不到」而不是「验证通过」，
        统一降级成默认值交由 verify_or_raise 判失败。"""
        # aqg: top-level boundary 回读取值失败一律降级为默认值，绝不当成验证通过
        try:
            return page.evaluate(js, arg)
        except Exception:
            return default

    def read_text(self, page, selector):
        """读任意元素的纯文本；元素不存在返回 ''。"""
        return self._safe_eval(page, READ_TEXT_JS, selector, "") or ""

    def body_text(self, page):
        return self.read_text(page, "body")

    def read_editor_state(self, page, editor_sel, title_sel=""):
        """回读编辑器正文 + 标题框值，取不到都给空串（判定交给调用方）。"""
        st = self._safe_eval(page, READ_STATE_JS, [editor_sel, title_sel], None)
        if not isinstance(st, dict):
            return {"body": "", "title": ""}
        return {"body": st.get("body") or "", "title": st.get("title") or ""}

    def wait_for_url_change(self, page, previous_url, accept=None,
                            timeout=25, poll=1.2):
        """轮询 URL，等它离开原地址并满足 accept 谓词；超时抛 PlatformError。

        previous_url 传 None 表示不要求「必须变化」，只看 accept 谓词。
        """
        deadline = time.time() + timeout
        seen = ""
        while time.time() < deadline:
            seen = page.url or ""
            moved = previous_url is None or seen != previous_url
            if moved and (accept is None or accept(seen)):
                return seen
            time.sleep(poll)
        raise PlatformError(
            f"[{self.name}] {timeout}s 内页面没跳到预期地址"
            f"（最后观察到：{seen or '空 URL'}）")

    def verify_text_present(self, page, texts, timeout=10, poll=1.0, scope="页面"):
        """轮询页面文本直到出现任一成功文案；超时抛 PlatformError。返回命中的文案。"""
        wanted = [t for t in (texts or []) if t]
        if not wanted:
            raise PlatformError(f"[{self.name}] verify_text_present 没给候选文案")
        deadline = time.time() + timeout
        body = ""
        while time.time() < deadline:
            body = self.body_text(page)
            hit = find_text(body, wanted)
            if hit:
                return hit
            time.sleep(poll)
        raise PlatformError(
            f"[{self.name}] {timeout}s 内{scope}没出现成功提示 {wanted}"
            f"（页面文本尾部：{(body or '')[-80:]!r}）")

    def require_content(self, page, md_content, title, editor_sel, title_sel, action):
        """一次性校验编辑器里的正文与标题就是目标内容，不符即抛。"""
        st = self.read_editor_state(page, editor_sel, title_sel)
        c_ok, c_detail = content_evidence(md_content, st["body"])
        self.verify_or_raise(action + "·正文", c_ok, c_detail)
        t_ok, t_detail = title_evidence(title, st["title"])
        self.verify_or_raise(action + "·标题", t_ok, t_detail)
        return st

    def save_debug(self, page, tag):
        from service.publishing.browser import dump_dom
        path = dump_dom(page, tag)
        return str(path)


ADAPTERS = {}


# 每个适配器声明自己真实实现的能力。数值来自各实现体：有方法且有实际赋值点才算 True，
# 仅"没抛 NotImplementedError"不算——那是自欺。
CAPABILITIES = {
    "bilibili":     {"publish": True, "draft": True, "update": True,  "tags": True},
    "cnblogs":      {"publish": True, "draft": True, "update": True,  "list": True,
                     "tags": True, "hosted_media": True},
    "csdn":         {"publish": True, "draft": True, "update": True,  "list": True,
                     "tags": True, "cover": True},
    "juejin":       {"publish": True, "draft": True, "update": True,  "list": True,
                     "tags": True, "cover": True},
    "metaweblog":   {"publish": True, "draft": True, "update": True,  "list": True,
                     "tags": True, "hosted_media": True},
    "oschina":      {"publish": True, "draft": True, "update": False, "list": False},
    "segmentfault": {"publish": True, "draft": True, "update": True,  "tags": True},
    "toutiao":      {"publish": True, "draft": True, "update": False, "list": False},
    "51cto":        {"publish": True, "draft": True, "update": True,  "list": True,
                     "tags": True, "hosted_media": True},
    "zhihu":        {"publish": True, "draft": True, "update": True,  "tags": True,
                     "question": True},
}


def capability_of(platform_id: str) -> dict:
    """返回某平台的能力矩阵（未注册/未声明时全 False）。"""
    base = dict(PlatformAdapter.capabilities)
    base.update(CAPABILITIES.get(platform_id, {}))
    return base


def register(cls):
    ADAPTERS[cls.id] = cls()
    return cls


def get_adapter(platform):
    if platform not in ADAPTERS:
        raise PlatformError(f"不支持的平台: {platform}，已注册: {list(ADAPTERS)}")
    return ADAPTERS[platform]
