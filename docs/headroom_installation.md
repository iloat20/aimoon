# Headroom 安装与使用指南

## 当前安装状态

✅ **npm 版本已安装**
- 包名：headroom-ai@0.1.0
- 位置：C:\Users\Administrator\AppData\Roaming\npm\node_modules\headroom-ai
- 类型：TypeScript/JavaScript 库

❌ **Python 版本未安装**
- 原因：需要 Microsoft C++ Build Tools 或 Rust 编译环境
- 系统缺少编译工具（Visual C++ 14.0+）

---

## 使用 npm 版本

### 1. 在 Node.js/TypeScript 项目中使用

```javascript
// ESM
import { compress } from 'headroom-ai';

// CommonJS
const { compress } = require('headroom-ai');

// 示例：压缩对话历史
const messages = [
  { role: 'user', content: 'Hello' },
  { role: 'assistant', content: 'Hi! How can I help?' },
];

const compressed = await compress(messages, {
  model: 'claude-3-5-sonnet-20241022',
});
```

### 2. 使用适配器

```javascript
// OpenAI 适配器
import { withHeadroom } from 'headroom-ai/openai';
import OpenAI from 'openai';

const client = withHeadroom(new OpenAI());

// Anthropic 适配器
import { withHeadroom } from 'headroom-ai/anthropic';
import Anthropic from '@anthropic-ai/sdk';

const client = withHeadroom(new Anthropic());

// Vercel AI SDK
import { headroomMiddleware } from 'headroom-ai/vercel-ai';
```

### 3. MCP 服务器（需要 Python 版本）

npm 版本不包含 MCP 服务器功能。需要 Python 版本才能使用：
- `headroom_compress`
- `headroom_retrieve`
- `headroom_stats`

---

## 安装 Python 版本（需要编译环境）

### 选项1：安装 Microsoft C++ Build Tools

1. 下载并安装：
   https://visualstudio.microsoft.com/visual-cpp-build-tools/

2. 安装时选择：
   - "C++ build tools"
   - Windows 10/11 SDK
   - MSVC v143 - VS 2022 C++ x64/x86 build tools

3. 安装完成后重启终端

4. 运行：
   ```bash
   pip install "headroom-ai[all]"
   ```

### 选项2：安装 Rust 工具链

1. 下载并安装：
   https://rustup.rs/

2. 安装完成后重启终端

3. 运行：
   ```bash
   pip install "headroom-ai[all]"
   ```

### 选项3：使用预编译的 wheel（如果有）

检查 PyPI 是否有预编译的 wheel：
```bash
pip install headroom-ai --only-binary=headroom-ai
```

---

## Headroom 功能概览

### 主要功能

1. **上下文压缩** - 减少 60-95% 的 token 使用
2. **多种压缩算法**
   - SmartCrusher（JSON）
   - CodeCompressor（AST）
   - Kompress-base（文本）
3. **可逆压缩（CCR）** - 原始内容可随时检索
4. **跨代理内存** - Claude、Codex、Gemini 共享
5. **MCP 服务器** - 与 Claude Code 集成

### 使用场景

- ✅ AI 代理的长期对话
- ✅ 大型代码库探索
- ✅ RAG（检索增强生成）
- ✅ 工具输出压缩
- ✅ 多代理协作

---

## 替代方案

如果无法安装 Python 版本，可以考虑：

### 1. 使用 npm 版本编程接口

在 Node.js/TypeScript 项目中直接使用：
```javascript
import { compress } from 'headroom-ai';
```

### 2. 使用其他上下文压缩工具

- **RTK** - CLI 输出压缩
- **lean-ctx** - CLI 和 MCP 工具
- **OpenAI Compaction** - 对话历史压缩（内置）

### 3. 手动优化上下文

- 清理无关的工具输出
- 压缩日志和调试信息
- 移除重复内容

---

## 下一步建议

### 如果你只需要编程接口：
✅ npm 版本已安装，可以直接使用

### 如果你需要命令行工具和 MCP：
1. 安装 Microsoft C++ Build Tools
2. 重启终端
3. 运行 `pip install "headroom-ai[all]"`

### 如果你想要完整功能：
1. 安装 Rust 工具链（https://rustup.rs/）
2. 重启终端
3. 运行 `pip install "headroom-ai[all]"`
4. 配置 MCP 服务器：`headroom mcp install`

---

## 参考资源

- 📖 **官方文档**：https://headroom-docs.vercel.app/docs
- 📦 **npm 包**：https://www.npmjs.com/package/headroom-ai
- 📦 **PyPI 包**：https://pypi.org/project/headroom-ai/
- 💻 **GitHub**：https://github.com/chopratejas/headroom
- 💬 **Discord**：https://discord.gg/yRmaUNpsPJ

---

*创建时间：2026-06-04*
