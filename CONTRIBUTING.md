# 贡献指南

感谢你对 **内容港 Harbour（harbour）** 的关注！欢迎以任何形式参与贡献：提 Issue、修 Bug、写文档、加功能都可以。

## 快速开始

1. Fork 本仓库到你的账号
2. 从 `main` 创建功能分支：`git checkout -b feat/your-feature`
3. 本地开发与运行方式见 [README](README.md)
4. 提交改动并发起 Pull Request，描述清楚改了什么、为什么改

## 提交规范

- 提交信息建议遵循 Conventional Commits：`feat: 新增xx`、`fix: 修复xx`、`docs: 文档`、`chore: 杂务`
- 每个提交聚焦单一改动，便于回溯与回滚
- 不要把密钥、个人数据提交进仓库

## 问题反馈

- 提 Issue 前请先搜索是否已有同类问题
- Bug 请附上复现步骤、预期/实际行为、环境信息
- 功能建议请说明使用场景

## 行为准则

参与贡献即表示同意遵守 [贡献者行为准则](CODE_OF_CONDUCT.md)。

## 发布流程（维护者）

每次对外更新按此清单执行，不跳步：

1. **定版本**：按语义化版本（SemVer）——修 Bug 升补丁位、新功能升次版本、破坏性改动升主版本
2. **同步文档**：改代码的同时更新 README（版本/截图，如界面有变）与依赖清单（pyproject.toml / uv.lock / package.json）
3. **本地验证**：跑通测试与构建（与 CI 同款命令），全绿才继续
4. **合并**：以 PR 方式合入 `main`，CI 全绿后合并
5. **打标签**：`git tag -a vX.Y.Z -m "vX.Y.Z"` 并推送（`git push origin vX.Y.Z`）
6. **发布**：基于该 tag 创建 GitHub Release，Release Notes 写清本次变更要点
7. **部署**：确认 GitHub Pages / 站点自动部署完成且可访问

变更记录以 GitHub Releases 与 git log 为准。
