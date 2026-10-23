<div align="center">

# FactGuard
### 让每条结论，都有出处。

**Evidence Intelligence · Source-grounded Research Review**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/53_core_tests-passing-168D72)](#本地运行)
[![Source](https://img.shields.io/badge/source-public-1C9C8A?logo=github)](https://github.com/ndshuge/factguard)

[核心代码](skill-package/scripts/) · [Skill 说明](skill-package/SKILL.md) · [证据契约](skill-package/references/contracts.md) · [演示页面](demo/index.html)

</div>

---

## 它解决什么问题？

技术报告最难的不是生成文字，而是证明**每一条结论如何回到原始材料**。

| 常见问题 | FactGuard 的处理 |
|---|---|
| 引用似乎正确，但找不到原文 | 记录文件、页码/段落/单元格与原文片段 |
| 不同来源的数值被错误比较 | 校验单位、时间、范围、定义和适用条件 |
| 修改后缺少依据 | 追踪审查异议、修改原因、再次复核 |
| 样例被当成正式报告 | 明确区分离线预览、完整报告和平台验证 |

## 工作流程

**原始材料 → 内容解析 → 来源绑定 → 证据审查 → 冲突修订 → 复查 → Word 成果**

- 多格式解析：PDF、DOCX、XLSX；保留来源位置与 SHA-256。
- 证据审查：拒绝缺失引文、遗漏否定条件或错误数值改写。
- 透明复核：不强行合并口径不同的结论，保留异议与适用边界。
- 正式出版：必须通过完整证据门禁，不能用短样例冒充完整报告。

## 仓库导览

| 路径 | 说明 |
|---|---|
| [Skill 主文件](skill-package/SKILL.md) | 功能、运行规则和证据边界 |
| [核心脚本](skill-package/scripts/) | 材料解析、审查、比较与渲染 |
| [数据契约](skill-package/references/contracts.md) | 可验证的输入输出格式 |
| [回归测试](tests/) | 核心离线测试 |
| [流程演示](demo/index.html) | 无需密钥的静态界面 |

## 本地运行

Python 3.10+；PDF 输入需要 PyMuPDF：

    python -m pip install pymupdf

进入 skill-package 目录，再运行：

    python scripts/factguard.py --help

从仓库根目录复验：

    $env:PYTHONPATH = "skill-package/scripts"
    python -m unittest discover -s tests -v

**公开整理版 53 项离线核心测试通过。** 这不等于竞赛平台正式得分。

## 安全与真实性

仓库不包含原生 TeleAgent 日志、用户原始文档、密钥、比赛 PDF 或视频。引用匹配只证明说法来自某项材料，**不能直接证明材料在现实世界中真实**。研究成果仍需人工核对和审查。

---

<div align="center"><sub>FactGuard · Evidence first, claims second · 2026</sub></div>
