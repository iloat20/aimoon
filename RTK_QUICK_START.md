# 🚀 RTK 快速使用指南

## ✅ RTK 已安装并配置

**版本**: rtk 0.42.1
**状态**: ✅ 已集成到 Claude Code
**位置**: ~/.local/bin/rtk.exe

---

## 🎯 基础用法

### 1. Git 操作（自动压缩）

```bash
# 重启 Claude Code 后，这些命令会自动压缩
git status           # 自动压缩 ✅
git log -n 10        # 自动压缩 ✅
git diff             # 自动压缩 ✅
git add .            # 自动压缩 ✅
git commit -m "msg"  # 自动压缩 ✅
```

### 2. 文件操作（手动使用）

```bash
rtk ls .                    # 压缩目录列表
rtk read main.rs            # 压缩文件内容
rtk grep "pattern" .        # 压缩搜索结果
rtk find "*.py" .           # 压缩文件查找
```

### 3. 测试和构建（手动使用）

```bash
rtk cargo test               # 压缩 Rust 测试输出
rtk pytest                   # 压缩 Python 测试输出
rtk npm test                 # 压缩 Node.js 测试输出
rtk cargo build              # 压缩构建输出
```

---

## 📊 查看统计

### Token 节省统计

```bash
rtk gain                    # 总体统计
rtk gain --graph            # 30 天图形
rtk gain --history          # 最近命令历史
rtk gain --daily            # 逐日统计
```

### 发现优化机会

```bash
rtk discover                # 发现未压缩的命令
rtk discover --all --since 7  # 最近 7 天的所有项目
```

### 查看采用率

```bash
rtk session                 # 显示 RTK 采用率
```

---

## 🔧 高级功能

### 初始化和配置

```bash
rtk init -g                 # 初始化（Claude Code/Copilot）
rtk init -g --gemini        # Gemini CLI
rtk init -g --codex         # Codex (OpenAI)
rtk init -g --agent cursor  # Cursor
rtk init --show             # 验证安装
```

### 查看帮助

```bash
rtk --help                  # 总体帮助
rtk git --help              # Git 命令帮助
rtk ls --help               # ls 命令帮助
```

---

## 💡 实际使用示例

### 示例 1：查看 git 状态

**标准输出**（~2000 tokens）：
```
On branch main
Your branch is up to date with 'origin/main'.

Changes not staged for commit:
  (use "git add <file>..." to update what will be committed)
  (use "git restore <file>..." to discard changes in working directory)
        modified:   src/main.rs
        modified:   Cargo.toml
...
```

**RTK 输出**（~200 tokens）：
```
* main...origin/main
 M src/main.rs
 M Cargo.toml
```

**节省**: 90% Token

### 示例 2：查看目录

**标准输出**（~800 tokens）：
```
drwxr-xr-x  15 user staff 480 ...
-rw-r-r--   1 user staff 1234 ...
...
```

**RTK 输出**（~150 tokens）：
```
my-project/
+-- src/ (8 files)
|   +-- main.rs
+-- Cargo.toml
```

**节省**: 80% Token

### 示例 3：运行测试

**标准输出**（~25,000 tokens）：
```
running 15 tests
test utils::test_parse ... ok
test utils::test_format ... ok
...
```

**RTK 输出**（~2,500 tokens）：
```
FAILED: 2/15 tests
  test_edge_case: assertion failed
  test_overflow: panic at utils.rs:18
```

**节省**: 90% Token

---

## 🎓 在 Claude Code 中使用

### 自动压缩（重启后）

✅ **所有 Bash 命令会自动压缩**

**重启 Claude Code 后**：
```bash
# 这些命令会自动通过 RTK 过滤
git status           # 自动压缩 ✅
ls -la               # 自动压缩 ✅
cargo test           # 自动压缩 ✅
git diff             # 自动压缩 ✅
```

### 手动压缩

```bash
# 在任何命令前加上 rtk
rtk <command>

# 示例
rtk cargo build
rtk ls -la
rtk git diff
rtk docker ps
```

---

## 📈 Token 节省统计

### 30 分钟 Claude Code 会话

| 操作 | 频率 | 标准 | rtk | 节省 |
|------|------|------|-----|------|
| `ls` / `tree` | 10x | 2,000 | 400 | **-80%** |
| `cat` / `read` | 20x | 40,000 | 12,000 | **-70%** |
| `grep` / `rg` | 8x | 16,000 | 3,200 | **-80%** |
| `git status` | 10x | 3,000 | 600 | **-80%** |
| `git diff` | 5x | 10,000 | 2,500 | **-75%** |
| `git log` | 5x | 2,500 | 500 | **-80%** |
| `cargo test` | 5x | 25,000 | 2,500 | **-90%** |
| **总计** | | **~118,000** | **~23,900** | **-80%** |

---

## 🔍 故障排除

### 问题：rtk 命令找不到

**解决**：
```bash
export PATH="$HOME/.local/bin:$PATH"
# 或添加到 ~/.bashrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### 问题：Claude Code 没有自动压缩

**解决**：
1. 检查 hooks：`rtk init --show`
2. **重启 Claude Code**
3. 手动测试：`rtk git status`

### 问题：某些命令没有压缩

**解决**：
- 查看支持的命令列表
- 使用 `rtk <command>` 显式调用
- 检查配置文件：`~/.config/rtk/config.toml`

---

## 📚 参考资源

### 文档
- 📖 **官方文档**：https://www.rtk-ai.app/guide
- 📖 **快速开始**：https://www.rtk-ai.app/guide/getting-started
- 📖 **配置指南**：https://www.rtk-ai.app/guide/getting-started/configuration
- 📖 **故障排除**：https://www.rtk-ai.app/guide/troubleshooting

### 社区
- 💻 **GitHub**：https://github.com/rtk-ai/rtk
- 💬 **Discord**：https://discord.gg/RySmvNF5kF

---

## 🎉 立即开始

### 步骤 1：重启 Claude Code
关闭并重新打开 Claude Code，所有 Bash 命令会自动压缩。

### 步骤 2：测试自动压缩
```bash
git status
ls -la
git diff
```

### 步骤 3：查看节省统计
```bash
rtk gain
rtk gain --graph
```

### 步骤 4：享受 60-90% Token 节省！
所有命令输出都会被自动压缩，显著减少 Token 使用。

---

*创建时间：2026-06-04*
*版本：1.0*
*状态：✅ 完成*
