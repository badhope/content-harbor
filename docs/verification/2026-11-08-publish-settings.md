# Verification Record — 2026-11-08 (publish settings, real accounts)

Commit: work tree on top of `df73113` (0.15.0).
Machine: Windows 10 19044, Python 3.14, Node 24, Edge 131 driven by patchright.
Method: **real browser, real logged-in accounts, real publish button.** No mocking.
Every screenshot referenced here was taken by the automation itself, at the moment
before the publish button was clicked.

Accounts used (all reported `logined: true` by `GET /accounts/{platform}/check`
immediately before the run):

| Platform | Account | Result |
|---|---|---|
| CSDN | default | **published** — article 170247396 |
| 知乎 | default | **published** — article 2102521480366114668 |
| 掘金 | default | settings filled, **publish blocked by platform** (see §3) |
| B站 | default | **blocked by platform** before editor loads (see §4) |

---

## 1. Why this round exists

The 0.15.0 read-back work answered "did the copy land on the platform". It did not
answer the other half of the requirement: **did the publish-settings panel get filled
in**. A post with no tags, no cover, no category and an empty summary is a published
post that nobody can find. The instruction was explicit: fill every field, show a
screenshot, only then publish.

What we found by actually opening the panels: **the code that was supposed to fill them
mostly did not work, and failed silently.**

## 2. CSDN — 12 fields, 11 filled, published

Panel structure, captured from the live DOM and confirmed against the screenshot:

| # | Field | Required | Mechanism that actually works |
|---|---|---|---|
| 1 | 文章标签 | **yes** | tag panel `.mark_selection_box`, chips render in `.form-tag-box .el-tag` |
| 2 | 添加封面 | no | `.el-upload__input` → then the **image editor dialog** must be confirmed |
| 3 | 文章摘要 | no | `textarea.el-textarea__inner` + native setter |
| 4 | 分类专栏 | no | `input.tag__option-chk` value match, click parent label |
| 5 | 文章类型 | **yes** | `<label for="original">` — input and label are **siblings** |
| 6 | 创作声明 | **yes** | `input[placeholder="无声明"]` + visible dropdown items |
| 7 | 文章备份 GitCode | no | checkbox inside the label containing the text |
| 8 | 可见范围 | **yes** | `<label for="public">` etc. |
| 9 | 文章模板 | **yes** | `<label for="article-template-simple-1">` (id has a random suffix) |
| 10 | 多平台发布 | no | `#multiPlatformPublishYes` / `No` |
| 11 | 参与活动 | no | dropdown, first item when unspecified |
| 12 | 话题 | no | dropdown — **empty for this account**, reported as failed, not skipped |

Result of the real run:

```
settings_filled:  [文章标签, 添加封面, 文章摘要, 分类专栏, 文章类型, 创作声明,
                   文章备份, 可见范围, 文章模板, 多平台发布,
                   参与活动=AtomGit「码动四季·开源同行」秋季征稿活动]
settings_failed:  [话题(没有可见下拉)]
post_id:          170247396
post_url:         https://blog.csdn.net/weixin_56622231/article/details/170247396
verify:           回读：已确认
```

Independent check: `GET` on that URL returns HTTP 200.

### Defects found and fixed on CSDN

All six were "the code ran, returned success, and did nothing". None was visible from
reading the code; each needed a screenshot to notice.

1. **Radio inputs are siblings of their labels, not children.** `label > input[type=radio]`
   never matches, so 文章类型 / 可见范围 / 文章模板 were all silently skipped. Fixed by
   clicking `label[for]` and reading `getElementById(for).checked`.
2. **The first `input[type=file]` on the page is the Markdown importer** (`accept=.md`),
   not the cover uploader (`.el-upload__input`, `accept=.png,.jpg`). The cover image was
   being handed to the Markdown importer — the panel stayed empty.
3. **The image-editor dialog must be confirmed.** After upload CSDN shows 图片编辑 with a
   「确认上传」 control that is a `<div class="vicp-operate-btn">`, not a button. Without
   clicking it the cover is never attached *and* the dialog covers the publish button.
4. **Two `el-select` dropdowns coexist** (活动 and 话题) because Element-Plus teleports
   them to `body`. Selecting globally picked the activity list when filling 话题 — the
   activity name "IT疑难杂症诊疗室" was written in as the topic. Fixed by filtering
   visible dropdowns by geometry and taking the correct one by vertical position.
5. **The tag panel (`.mark_selection_box`) covers half the dialog** until explicitly
   closed. Left open, the radio groups underneath cannot be clicked and the screenshot
   shows nothing. Fixed by closing that panel specifically (not the dialog's own X).
6. **Multi-platform publish matched on label text** containing both "是" and "多平台";
   the label only says "是", so it never matched. Fixed with the stable element ids.

Regression tests: `backed/tests/test_csdn_publish_settings.py` (7 tests). They assert
every one of the 12 fields is accounted for in filled/skipped/failed, that detail counts
match the lists, and that the sibling-label / `.el-upload__input` / visible-dropdown
mechanisms stay in place.

## 3. 掘金 — settings fill works, publishing is blocked by the platform

Panel structure (screenshot confirmed): 分类\*, 添加标签\*, 文章封面, 收录至专栏,
创作话题, 编辑摘要\*.

Filled and verified by reading the panel back:

```
settings_filled: [分类=后端, 标签=Python,后端,自动化运维, 文章封面, 摘要(24字)]
settings_failed: []
```

So the panel side works. The publish step does not, and this is not our code:

- Clicking 「确定并发布」 produces **exactly one** network request:
  `POST /content_api/v1/article_draft/detail` — a *read*. No publish call is made at
  all. Confirmed by capturing responses during the click.
- The legacy `POST /content_api/v1/article/publish` endpoint still exists (it answers
  `参数错误`, not `路由不存在`), so we tried to drive it directly. It rejects every
  payload shape tried: full draft object, minimal required set, `tag_ids` as array of
  strings, as JSON string, with/without `article_id`, `theme_ids` as `[]` and `[]string`
  — the error alternates between `参数错误` and Go unmarshal complaints, so the actual
  required schema is not the draft schema. Reversing it further was out of proportion
  for this round.

**Honest status:** the draft is created with all settings attached and is visible in the
creator draft list; the article is **not published**. The adapter returns
`draft_only: true` / `status: pending_human` with a warning, so the UI does not claim
success. This matches the pre-existing note in the adapter ("掘金 2026-09 publish API
已封死") — this round confirmed it with a network trace rather than assuming it.

## 4. B站 — editor never loads

`member.bilibili.com/platform/upload/text` renders its shell and the draft list, but the
column editor route (`/text/new-article`, and the older
`/upload-manager/article/editor`) comes up blank with a banner reading
`timeout of 10000ms exceeded`. No input, no editor, no `<iframe>` containing either —
only the message/read-draft iframes. This reproduces across repeated attempts and
profile cleanups.

That is a platform-side load failure in this network, not something the adapter can
work around: there is no DOM to drive. Recorded as blocked; nothing claimed.

## 5. 知乎 — 4 fields, 3 filled, published

Panel is **not** a post-click dialog: 发布设置 is a permanent block on the edit page.
The old code filled it *after* clicking 发布, by which point the page is navigating away
— which is why the topics/AI-assist/activity selectors it used never matched anything.

Real structure and result:

| Field | Mechanism | Result |
|---|---|---|
| 添加封面 | `label.UploadPicture-wrapper input[type=file]` | filled (preview verified) |
| 投稿至问题 | text input next to that label | skipped — no question URL given |
| 专栏收录 | `label` with exact text 发布到专栏 / 不发布到专栏 | filled (不发布到专栏) |
| 定时发布 | `input.css-1i5t6yb` type=checkbox | filled (left off deliberately) |

```
post_id:   2102521480366114668
post_url:  https://zhuanlan.zhihu.com/p/2102521480366114668
settings:  已填 3 项，跳过 1（投稿至问题）
```

Independent check inside the logged-in browser: the article page renders with
`<h1>Harbor 全流程发布验证：知乎发布设置逐项填满</h1>` and the body text.
(A bare `curl` gets HTTP 403 — 知乎 blocks anonymous requests; that is expected and is
why the check was done in the logged-in browser.)

### Defect found on 知乎

**`<title>` carries a per-user notification prefix**, e.g.
`(13 封私信 / 44 条消息) Harbor 全流程发布验证… - 知乎`. Read-back compared the whole
string against the target and reported 标题不符 for an article whose heading was
character-for-character correct. Fixed twice over:

- `_strip_site_suffix` now strips bracketed prefixes `(…)` `（…）` `【…】` `[…]` in
  addition to trailing site suffixes (`_CSDN博客`, ` - 知乎`).
- page read-back prefers `<h1>` over `<title>`, since on an article page the `h1` *is*
  the title and carries none of the site chrome.

Regression tests added to `backed/tests/test_title_evidence.py` (now 13 cases).

## 6. What is still not verified

- 掘金 publishing. Settings yes, publish no — platform API rejects every schema we could
  derive. Needs either a browser capture of a successful manual publish request, or a
  platform-side change.
- B站. Editor does not load in this network; nothing was driven.
- The other eleven platforms in the capability matrix were not exercised this round.
- Read-back for 掘金/B站 is untested end to end because nothing was published there.

---

## 7. 原地更新（管理链路）— CSDN 已通，且抓到四个连环假成功

发布只是半边。用户要求「能发上去**能管理**」，所以同一篇文章还要能改。

验证方法：改本地正文 → 调 `update_single` → 再用浏览器打开**线上文章页**，
确认新段落真的出现在页面上（不是只看函数返回值）。

结果：通过。线上文章
`https://blog.csdn.net/weixin_56622231/article/details/170247396` 已包含
「最终更新验证」段落，正文长度从 2942 增到 3454。

达到这个结果之前，连续踩了四个"返回成功但没生效"：

1. **`content_hash` 提前写死。** 第一次更新失败后哈希已被更新成新值，之后每次
   调用都在 `pub_hash == _content_hash(article)` 处提前 return
   `{"ok": True, "skipped": "内容未变化"}` —— 一次都没真正请求平台，却一直报成功。
2. **`_import_md_file` 在"已有内容"场景下无声失败。** 它返回 `True`，但编辑器
   里还是原文（CSDN 的导入只往空编辑器填）。发布时它好用，更新时不能用。
   改用「点进编辑器 → Ctrl+A → Delete → `keyboard.insert_text`」，并在注入后
   回读编辑器正文确认，否则中止（不把旧稿重发一遍）。
3. **确认按钮点到「定时发布」。** 弹窗底栏是 取消 / 保存为草稿 / 定时发布 /
   发布文章，其中两个带 `btn-b-red`。原实现取"最后一个 `btn-b-red`"命中了
   定时发布 —— 弹窗不关、无网络请求、函数返回 True。改为按文案精确匹配
   「发布文章」。
4. **摘要没填进弹窗。** `document.querySelector('textarea.el-textarea__inner')`
   选中了页面上别的 textarea（AI 助手输入框），摘要栏停在 6/256。CSDN 要求
   ≥10 字才允许发布，于是点「发布文章」**没有任何网络请求**——看起来像按钮坏了。
   改为在 `.modal__inner-2` 作用域内取 textarea，并回读确认字数。

另外两处必须处理的环境差异：

- 编辑器是异步渲染的，更新页比新建页更慢。不等就拿到 `None`，直接返回
  `no_editor`。现在最多等 20s。
- 发布成功后 CSDN 会整页跳转到 `creation/success/<id>`，此时 `page.evaluate`
  抛 `Execution context was destroyed`。那是成功信号，不是失败——判定逻辑
  同时接受"跳转到 success 页"和"弹窗消失"。

新增回归测试（`test_csdn_publish_settings.py`，共 10 个）锁住：
`update` 必须接受 `options`、确认按钮必须文案匹配、更新后必须有线上回读。
