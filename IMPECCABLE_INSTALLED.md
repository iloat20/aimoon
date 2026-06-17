# ✅ Impeccable 技能安装完成

## 安装信息

**技能**: Impeccable（前端设计技能）
**版本**: 3.5.0
**位置**: ~/.claude/skills/impeccable/
**状态**: ✅ 已安装

---

## 安装命令

```bash
# 方法 1：CLI installer（推荐）
cd /tmp/impeccable
npx impeccable skills install

# 方法 2：手动复制
cp -r /tmp/impeccable/.claude/skills/impeccable ~/.claude/skills/
```

---

## 主要功能

### ✅ 23 个设计命令

所有命令通过 `/impeccable` 访问：

#### 核心流程
- ✅ `/impeccable craft` - 完整的形状-构建流程
- ✅ `/impeccable init` - 一次性设置
- ✅ `/impeccable shape` - 在写代码前规划 UX/UI
- ✅ `/impeccable document` - 生成 DESIGN.md

#### 审查和优化
- ✅ `/impeccable critique` - UX 设计审查
- ✅ `/impeccable audit` - 技术质量检查
- ✅ `/impeccable polish` - 最终优化

#### 设计调整
- ✅ `/impeccable bolder` - 放大无聊的设计
- ✅ `/impeccable quieter` - 调低过于大胆的设计
- ✅ `/impeccable distill` - 提炼到本质

#### 视觉增强
- ✅ `/impeccable animate` - 添加运动
- ✅ `/impeccable colorize` - 引入颜色
- ✅ `/impeccable typeset` - 修复字体
- ✅ `/impeccable layout` - 修复布局

#### 用户体验
- ✅ `/impeccable harden` - 错误处理
- ✅ `/impeccable optimize` - 性能优化
- ✅ `/impeccable adapt` - 适应设备

---

### ✅ 7 个领域参考文件

| 参考文件 | 覆盖内容 |
|---------|---------|
| **typography** | 字体系统、字体配对、模块化比例 |
| **color-and-contrast** | OKLCH、着色中性色、深色模式 |
| **spatial-design** | 间距系统、网格、视觉层次 |
| **motion-design** | 缓动曲线、交错、减少运动 |
| **interaction-design** | 表单、焦点状态、加载模式 |
| **responsive-design** | 移动优先、流体设计 |
| **ux-writing** | 按钮标签、错误消息、空状态 |

---

### ✅ 27 个反模式规则

**绝对禁止的设计模式**：
- ❌ 侧边条边框（`border-left` 或 `border-right` > 1px）
- ❌ 渐变文本（`background-clip: text` + 渐变背景）
- ❌ 作为默认的毛玻璃效果
- ❌ 英雄指标模板（大数字 + 小标签 + 渐变强调）
- ❌ 相同的卡片网格
- ❌ 每个部分上方的小型大写跟踪眉毛

**设计指导**：
- ✅ 验证对比度（正文 ≥4.5:1，大文本 ≥3:1）
- ✅ 限制字体族数量 ≤3
- ✅ 使用 OKLCH 颜色空间
- ✅ 着色中性色（添加 0.005-0.015 色度）

---

## 使用指南

### 自动触发

✅ **技能会自动触发**

**示例**：
```
/impeccable audit blog           # 审计博客页面
/impeccable critique landing     # UX 设计审查
/impeccable polish settings      # 发布前优化
/impeccable harden checkout      # 添加错误处理
```

### 手动触发

```bash
/impeccable craft
/impeccable shape
/impeccable critique
/impeccable audit
/impeccable polish
```

### 创建快捷方式

```bash
/impeccable pin audit    # 创建 /audit 快捷方式
/impeccable pin polish   # 创建 /polish 快捷方式
```

---

## 实际使用示例

### 示例 1：设计新页面

**流程**：
1. 你："我想设计一个登录页面"
2. Claude：自动触发 `/impeccable shape`
3. Claude：规划 UX/UI 结构
4. 你：确认设计方向
5. Claude：自动触发 `/impeccable craft`
6. Claude：生成完整的设计代码
7. 你：审查并提供反馈
8. Claude：自动触发 `/impeccable polish`
9. Claude：最终优化和发布准备

### 示例 2：审查现有设计

**流程**：
1. 你："请审查这个登录页面"
2. Claude：自动触发 `/impeccable critique`
3. Claude：UX 设计审查
4. Claude：提供改进建议
5. 你：接受建议
6. Claude：自动触发 `/impeccable polish`
7. Claude：应用改进

### 示例 3：优化性能

**流程**：
1. 你："这个页面加载很慢"
2. Claude：自动触发 `/impeccable audit`
3. Claude：运行性能检查
4. Claude：识别问题
5. Claude：自动触发 `/impeccable optimize`
6. Claude：应用优化

### 示例 4：添加动画

**流程**：
1. 你："这个页面感觉很死板"
2. Claude：自动触发 `/impeccable animate`
3. Claude：添加有目的的运动
4. 你：审查动画效果
5. Claude：调整参数

---

## 设计原则

### 颜色

✅ **必须遵守**：
- 验证对比度（正文 ≥4.5:1，大文本 ≥3:1）
- 使用 OKLCH 颜色空间
- 着色中性色（添加 0.005-0.015 色度）
- 避免灰色文本在彩色背景上

### 排版

✅ **必须遵守**：
- 限制字体族数量 ≤3
- 正文行长度上限 65-75ch
- 层次通过比例 + 权重对比（≥1.25 比例）
- 使用 `text-wrap: balance` 在 h1-h3 上

### 布局

✅ **必须遵守**：
- 变化间距以创造节奏
- 卡片是懒惰的答案，仅在真正最佳时使用
- 嵌套卡片总是错误的
- Flexbox 用于 1D，Grid 用于 2D

### 运动

✅ **必须遵守**：
- 运动应该是有意的，不是事后添加
- 不要动画 CSS 布局属性
- 使用指数曲线的 ease-out
- 减少运动不是可选的（`@media (prefers-reduced-motion: reduce)`）

---

## 参考文件

### 领域参考

```bash
# 查看所有参考文件
ls ~/.claude/skills/impeccable/reference/

# 查看特定参考
cat ~/.claude/skills/impeccable/reference/typography.md
cat ~/.claude/skills/impeccable/reference/color-and-contrast.md
cat ~/.claude/skills/impeccable/reference/spatial-design.md
cat ~/.claude/skills/impeccable/reference/motion-design.md
```

### 命令参考

```bash
# 查看命令参考
cat ~/.claude/skills/impeccable/reference/craft.md
cat ~/.claude/skills/impeccable/reference/shape.md
cat ~/.claude/skills/impeccable/reference/audit.md
cat ~/.claude/skills/impeccable/reference/critique.md
cat ~/.claude/skills/impeccable/reference/polish.md
```

---

## 验证安装

### 检查技能文件

```bash
ls -la ~/.claude/skills/impeccable/
```

**输出**：
```
total 104
drwxr-xr-x  1 Administrator 197121     0 Jun  4 09:17 ./
drwxr-xr-x  3 Administrator 197121     0 Jun  4 09:17 ../
-rw-r--r--  1 Administrator 197121 21006 Jun  4 09:17 SKILL.md
drwxr-xr-x  2 Administrator 197121     0 Jun  4 09:17 reference/
drwxr-xr-x  2 Administrator 197121     0 Jun  4 09:17 scripts/
```

### 检查参考文件

```bash
ls ~/.claude/skills/impeccable/reference/
```

**输出**：
```
typography.md
color-and-contrast.md
spatial-design.md
motion-design.md
interaction-design.md
responsive-design.md
ux-writing.md
brand.md
product.md
```

---

## 下一步

### 立即使用

✅ **已完成** - Impeccable 已安装

1. **开始新设计**
   - 在 Claude Code 中描述你的设计需求
   - Claude 会自动触发 `/impeccable shape`

2. **审查现有设计**
   - 请求审查："/impeccable critique [页面名称]"
   - 查看改进建议

3. **优化设计**
   - 运行审计："/impeccable audit [页面名称]"
   - 应用优化

### 深入学习

1. 查看技能文档
   ```bash
   cat ~/.claude/skills/impeccable/SKILL.md
   ```

2. 查看参考文件
   ```bash
   cat ~/.claude/skills/impeccable/reference/typography.md
   ```

3. 访问官方网站
   - https://impeccable.style

---

## 更新技能

### 检查更新

```bash
cd /tmp/impeccable
git pull
npx impeccable skills install --force
```

### 重新安装

```bash
rm -rf ~/.claude/skills/impeccable
cp -r /tmp/impeccable/.claude/skills/impeccable ~/.claude/skills/
```

---

## 卸载技能

```bash
rm -rf ~/.claude/skills/impeccable
```

---

## 参考资源

### 文档
- 📖 **官方网站**：https://impeccable.style
- 📖 **GitHub**：https://github.com/pbakaus/impeccable
- 📖 **技能文档**：~/.claude/skills/impeccable/SKILL.md
- 📖 **参考文件**：~/.claude/skills/impeccable/reference/

---

## 总结

✅ **Impeccable 3.5.0 已成功安装**

### 已完成
- ✅ 技能文件已复制到 ~/.claude/skills/
- ✅ 23 个设计命令已准备就绪
- ✅ 7 个领域参考文件已加载
- ✅ 27 个反模式规则已配置
- ✅ 自动触发机制已启用

### 主要功能
- 🎨 **设计流程** - craft、shape、polish
- 🔍 **审查和优化** - critique、audit、optimize
- 🎭 **视觉增强** - animate、colorize、typeset
- 🛡️ **质量保证** - harden、adapt、distill

### 预期效果
- 📈 **设计质量提升** - 专业级前端设计
- ⚡ **开发效率提升** - 自动化设计流程
- 🎯 **设计一致性** - 设计系统对齐
- ♿ **无障碍保证** - 自动检查对比度和无障碍

### 下一步
1. **开始新设计** - 描述需求，触发 `/impeccable shape`
2. **审查现有设计** - 请求 `/impeccable critique`
3. **优化设计** - 运行 `/impeccable audit`
4. **享受专业级前端设计** - Impeccable 已就绪！

---

**安装完成！** ✅

*创建时间：2026-06-04*
*版本：1.0*
*状态：✅ 完成*
