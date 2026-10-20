# 仓库绑定与同步规则

本项目正式名称为 **MAPAS**。代码与文档的唯一工作远端是
[pbw1212/nlp-](https://github.com/pbw1212/nlp-)；Git 远端名为 `origin`，主分支为 `main`。

当前本机工作副本为 `D:\MAPAS\MAPAS`。项目名称、文件夹名和历史文档中的旧仓库记录都不能用来推断当前远端。

## Git 操作前的强制核验

在 fetch、pull、push、创建 PR 或同步分支前，先检查仓库根目录、远端、分支和工作区：

```powershell
git rev-parse --show-toplevel
git remote -v
git branch -vv
git status --short --branch
git remote get-url origin
```

`origin` 必须精确指向 `https://github.com/pbw1212/nlp-`，当前分支应为 `main` 并跟踪 `origin/main`。远端缺失、URL 不一致或仓库根目录不符时，停止 Git 网络操作并先核对本文件；不得根据记忆、目录名或历史记录猜测远端。本项目 Git 网络操作只使用 `origin`。

带日期的维护报告、评测和发布记录可能保留当时的仓库地址、路径和提交信息。这些字段只用于还原对应时点，不是当前远端配置；当前映射以本文件及 Git 的 `origin` 配置为准。
