# compare/ — 本地基准与实验结果

本目录存放 **K-Fusion cyclictest 长测**、论文三基线对比、Jailhouse enable 日志等。

## 与 Git 的关系（重要）

| 内容 | 是否纳入 Git |
|------|----------------|
| 本文件 `README.md` | ✅ 是 |
| `.gitignore` | ✅ 是 |
| `kfusion/soak/`、`stress/`、`paper/`、`crtos/` 下所有数据 | ❌ **否** |

根目录 [`.gitignore`](../.gitignore) 规则：

```gitignore
compare/*
!compare/README.md
!compare/.gitignore
```

因此：**克隆仓库后 `compare/` 除 README 外为空**；本地跑长测后才会生成 bin/log。  
`results/` 是指向本目录的符号链接，同样不跟踪测量数据。

## 子目录（本地生成）

| 子目录 | 内容 |
|--------|------|
| `kfusion/soak/`、`stress/` | K-Fusion 浸泡 / 加压 bin、log |
| `paper/` | 论文图表与 JSON |
| `crtos/` | Jailhouse enable 调试 log |

首次使用可手动创建：`mkdir -p compare/kfusion/{soak,stress}`。
