# ✅ Headroom 安装总结

## 安装状态

### npm 版本 - 已安装 ✅
```bash
npm install -g headroom-ai@0.1.0
```
- **位置**：C:\Users\Administrator\AppData\Roaming\npm\node_modules\headroom-ai
- **类型**：TypeScript/JavaScript 库
- **功能**：编程接口（不包含命令行工具）

### Python 版本 - 未安装 ❌
```bash
pip install "headroom-ai[all]"  # 失败
```
- **原因**：需要 Microsoft C++ Build Tools 或 Rust 编译环境
- **错误**：缺少 Visual C++ 14.0+

---

## 已安装的功能

### ✅ npm 版本提供的功能

1. **编程接口** - 在 Node.js/TypeScript 项目中使用
2. **上下文压缩** - 减少 60-95% 的 token 使用
3. **适配器支持**
   - OpenAI SDK 集成
   - Anthropic SDK 集成
   - Vercel AI SDK 集成

### ❌ npm 版本不提供的功能

1. **命令行工具** (`headroom` 命令)
2. **MCP 服务器** (需要 Python 版本)
3. **代理包装** (`headroom wrap claude`)
4. **代理模式** (`headroom proxy`)
5. **学习功能** (`headroom learn`)
6. **Kompress-base 模型** (需要 Python + ML 依赖)

---

## 使用指南

### 在项目中使用 npm 版本

#### 1. 作为 ES 模块导入
```javascript
import { compress } from 'headroom-ai';

const messages = [
  { role: 'user', content: 'Hello' },
  { role: 'assistant', content: 'Hi!' },
];

const compressed = await compress(messages, {
  model: 'claude-3-5-sonnet-20241022',
});
```

#### 2. 使用 OpenAI 适配器
```javascript
import { withHeadroom } from 'headroom-ai/openai';
import OpenAI from 'openai';

const client = withHeadroom(new OpenAI());
// 正常使用，压缩自动应用
```

#### 3. 使用 Anthropic 适配器
```javascript
import { withHeadroom } from 'headroom-ai/anthropic';
import Anthropic from '@anthropic-ai/sdk';

const client = withHeadroom(new Anthropic());
// 正常使用，压缩自动应用
```

---

## 安装 Python 版本（完整功能）

### 选项1：安装 Microsoft C++ Build Tools（推荐）

**步骤：**

1. 下载 Build Tools
   https://visualstudio.microsoft.com/visual-cpp-build-tools/

2. 运行安装程序

3. 选择工作负载：
   - ✅ "C++ build tools"
   - ✅ Windows 10/11 SDK
   - ✅ MSVC v143 - VS 2022 C++ x64/x86 build tools

4. 点击"安装"

5. **重启终端**

6. 安装 headroom：
   ```bash
   pip install "headroom-ai[all]"
   ```

**优点**：
- 最简单的解决方案
- 支持所有 Python 原生扩展

### 选项2：安装 Rust 工具链

**步骤：**

1. 下载 rustup
   https://rustup.rs/

2. 运行安装程序（选择默认选项）

3. **重启终端**

4. 安装 headroom：
   ```bash
   pip install "headroom-ai[all]"
   ```

**优点**：
- 安装更轻量
- Rust 本身就是很好的工具

### 选项3：使用预编译的 wheel（如果可用）

```bash
pip install headroom-ai --only-binary=headroom-ai
```

**注意**：可能没有适用于 Windows 的预编译 wheel

---

## Headroom 功能对比

| 功能 | npm 版本 | Python 版本 |
|------|---------|------------|
| 编程接口 | ✅ | ✅ |
| 命令行工具 | ❌ | ✅ |
| MCP 服务器 | ❌ | ✅ |
| 代理包装 | ❌ | ✅ |
| 代理模式 | ❌ | ✅ |
| 学习功能 | ❌ | ✅ |
| Kompress-base | ❌ | ✅ |
| 跨代理内存 | ❌ | ✅ |

---

## 使用场景

### ✅ npm 版本适合

1. **Node.js/TypeScript 项目**
   - Web 应用
   - API 服务
   - 工具库

2. **编程集成**
   - 在代码中压缩对话
   - 与 OpenAI/Anthropic SDK 集成
   - 自定义压缩逻辑

3. **前端应用**
   - React/Vue/Angular 应用
   - 浏览器扩展

### ✅ Python 版本适合

1. **AI 代理开发**
   - Claude Code
   - Codex
   - Cursor
   - 自定义代理

2. **命令行工具**
   - 自动化脚本
   - CI/CD 集成

3. **MCP 生态**
   - Claude Desktop
   - 其他 MCP 客户端

---

## 替代方案

如果无法安装 Python 版本，可以考虑：

### 1. 其他上下文压缩工具

| 工具 | 特点 |
|------|------|
| **RTK** | CLI 输出压缩（轻量） |
| **lean-ctx** | CLI 和 MCP 工具 |
| **OpenAI Compaction** | 内置（仅限 OpenAI） |

### 2. 手动优化

- 清理无关的工具输出
- 压缩日志和调试信息
- 移除重复内容
- 使用更简洁的提示

### 3. 使用云端服务

一些服务提供在线压缩：
- Compresr (compresr.ai)
- Token Co. (thetokencompany.ai)

**注意**：需要发送数据到外部服务器

---

## 下一步行动

### 如果你只需要编程接口：
✅ **已完成** - npm 版本已安装

使用示例：
```bash
# 查看示例代码
cat examples/headroom_usage.js
```

### 如果你需要完整功能：
1. 安装 Microsoft C++ Build Tools
2. 重启终端
3. 运行 `pip install "headroom-ai[all]"`
4. 配置 MCP 服务器

### 如果你想先试用：
1. 查看 npm 版本文档
2. 在小项目中测试
3. 评估是否需要 Python 版本

---

## 参考资源

### 文档
- 📖 **官方文档**：https://headroom-docs.vercel.app/docs
- 📖 **快速开始**：https://headroom-docs.vercel.app/docs/quickstart
- 📖 **安装指南**：https://headroom-docs.vercel.app/docs/installation

### 包管理
- 📦 **npm 包**：https://www.npmjs.com/package/headroom-ai
- 📦 **PyPI 包**：https://pypi.org/project/headroom-ai/

### 社区
- 💻 **GitHub**：https://github.com/chopratejas/headroom
- 💬 **Discord**：https://discord.gg/yRmaUNpsPJ

---

## 创建的文件

1. ✅ `docs/headroom_installation.md` - 详细安装指南
2. ✅ `examples/headroom_usage.js` - 使用示例
3. ✅ `HEADROOM_INSTALLATION_SUMMARY.md` - 本文件

---

## 总结

✅ **npm 版本已安装** - 可在 Node.js/TypeScript 项目中使用

❌ **Python 版本未安装** - 需要编译环境（C++ Build Tools 或 Rust）

**建议**：
- 如果需要编程接口 → npm 版本足够
- 如果需要命令行和 MCP → 安装 Python 版本

**下一步**：
1. 查看 `examples/headroom_usage.js` 了解使用方法
2. 根据需求决定是否安装 Python 版本
3. 参考官方文档深入学习

---

*创建时间：2026-06-04*
*版本：1.0*
