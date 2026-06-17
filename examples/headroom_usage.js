/**
 * Headroom npm 版本使用示例
 *
 * 这个示例展示如何使用 headroom-ai 的 TypeScript/JavaScript 版本
 * 来压缩 LLM 对话历史，减少 token 使用。
 */

// 注意：这是示例代码，实际使用时需要根据你的项目结构调整导入

// ==================== 基础用法 ====================

/**
 * 示例 1：压缩对话消息
 */
async function compressConversation() {
  // 动态导入（ESM）
  const { compress } = await import('headroom-ai');

  const messages = [
    {
      role: 'user',
      content: '请帮我分析这段代码的功能和可能的改进点。'
    },
    {
      role: 'assistant',
      content: `这段代码实现了以下功能：
1. 读取配置文件
2. 初始化数据库连接
3. 创建 HTTP 服务器
4. 定义 API 路由
5. 启动监听

改进建议：
- 添加错误处理
- 实现配置验证
- 添加日志记录
- 实现优雅关闭
- 添加健康检查端点`
    },
    {
      role: 'user',
      content: '请详细说明错误处理的最佳实践。'
    },
    {
      role: 'assistant',
      content: `错误处理最佳实践：

1. **分层错误处理**
   - 业务逻辑层抛出自定义错误
   - 中间件层捕获并转换为 HTTP 响应
   - 全局异常处理器捕获未预期的错误

2. **错误类型分类**
   - 用户输入错误（400）
   - 认证/授权错误（401/403）
   - 资源不存在（404）
   - 服务器内部错误（500）
   - 外部服务错误（502/503）

3. **错误响应格式**
   {
     "error": {
       "code": "VALIDATION_ERROR",
       "message": "Invalid email format",
       "details": {...}
     }
   }

4. **日志记录**
   - 记录错误堆栈
   - 包含请求上下文
   - 区分错误级别（warn/error/fatal）

5. **监控和警报**
   - 错误率监控
   - 关键错误实时警报
   - 错误趋势分析`
    }
  ];

  try {
    const compressed = await compress(messages, {
      model: 'claude-3-5-sonnet-20241022',
      // 可选：指定压缩目标
      targetTokens: 1000,
    });

    console.log('Original messages:', messages.length);
    console.log('Compressed tokens:', compressed.usage?.totalTokens);
    console.log('Savings:', compressed.savings);
  } catch (error) {
    console.error('Compression failed:', error);
  }
}

// ==================== OpenAI 集成 ====================

/**
 * 示例 2：与 OpenAI SDK 集成
 */
async function openaiIntegration() {
  const OpenAI = require('openai');
  const { withHeadroom } = require('headroom-ai/openai');

  // 创建带压缩的 OpenAI 客户端
  const client = withHeadroom(new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
  }));

  // 正常使用，压缩自动应用
  const response = await client.chat.completions.create({
    model: 'gpt-4',
    messages: [
      { role: 'user', content: 'Explain quantum computing' }
    ],
  });

  console.log('Response:', response.choices[0].message.content);
}

// ==================== Anthropic 集成 ====================

/**
 * 示例 3：与 Anthropic SDK 集成
 */
async function anthropicIntegration() {
  const Anthropic = require('@anthropic-ai/sdk');
  const { withHeadroom } = require('headroom-ai/anthropic');

  // 创建带压缩的 Anthropic 客户端
  const client = withHeadroom(new Anthropic({
    apiKey: process.env.ANTHROPIC_API_KEY,
  }));

  // 正常使用，压缩自动应用
  const response = await client.messages.create({
    model: 'claude-3-5-sonnet-20241022',
    max_tokens: 1024,
    messages: [
      { role: 'user', content: 'Explain machine learning' }
    ],
  });

  console.log('Response:', response.content[0].text);
}

// ==================== Vercel AI SDK 集成 ====================

/**
 * 示例 4：与 Vercel AI SDK 集成
 */
async function vercelAISDKIntegration() {
  const { generateText } = require('ai');
  const { openai } = require('@ai-sdk/openai');
  const { headroomMiddleware } = require('headroom-ai/vercel-ai');

  // 使用中间件
  const result = await generateText({
    model: openai('gpt-4'),
    messages: [
      { role: 'user', content: 'Explain neural networks' }
    ],
    experimental_providerOptions: {
      middleware: headroomMiddleware(),
    },
  });

  console.log('Result:', result.text);
}

// ==================== 主函数 ====================

async function main() {
  console.log('=== Headroom npm 版本示例 ===\n');

  console.log('1. 基础压缩示例');
  await compressConversation();

  console.log('\n2. OpenAI 集成（需要 API 密钥）');
  // await openaiIntegration();

  console.log('\n3. Anthropic 集成（需要 API 密钥）');
  // await anthropicIntegration();

  console.log('\n4. Vercel AI SDK 集成（需要 API 密钥）');
  // await vercelAISDKIntegration();

  console.log('\n=== 提示 ===');
  console.log('- npm 版本是库，不提供命令行工具');
  console.log('- 需要 Python 版本才能使用 MCP 服务器');
  console.log('- 安装 Python 版本需要 C++ Build Tools 或 Rust');
  console.log('- 文档：https://headroom-docs.vercel.app/docs');
}

// 导出供外部使用
module.exports = {
  compressConversation,
  openaiIntegration,
  anthropicIntegration,
  vercelAISDKIntegration,
};

// 如果直接运行
if (require.main === module) {
  main().catch(console.error);
}
