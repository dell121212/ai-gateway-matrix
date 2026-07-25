# 逆向 / 语法审计报告（2026-07-24）

范围：交付 deb、保护管线、授权闸、启动脚本、专业后台模块。  
性质：自有交付物加固审计 + 语法/逻辑 bug 修复（非攻击外部系统）。

## 检查结果摘要

| 项 | 结果 |
|----|------|
| AST 解析 `gateway`/`dashboard`/`packaging/protect` 等 | **95 文件 0 语法错误** |
| `bash -n` 关键脚本 | **通过** |
| `pytest` protect + run_script + billing | **41 passed** |
| 保护 deb 解包洁癖 | **通过**（无 watermark_rules、无 frontend/src、无 test_gateway） |
| 保护载荷 import（无 cwd 污染） | **`gateway._wm` + hook 正常** |

## 对手路径复盘（客户 deb）

| 步 | 动作 | 所见 | 处置 |
|----|------|------|------|
| 1 | `dpkg-deb -x` | `/usr/share/...` 程序树，无用户 Key | 预期 |
| 2 | 找 `.py` | 有（Python 交付形态）；已去 `#` 注释与模块 docstring | 保护构建 |
| 3 | 找假密钥 `sk-...` | 原 `scripts/test_gateway.py` 有样例 | **已从客户包剔除** |
| 4 | 找 `watermark_rules.json` | 无 | OK |
| 5 | 丢 AI | 无设计原则长注释；有 `_wm` 诱饵常量 | 提高成本 |
| 6 | 动态 | 仍可运行时分析 | 诚实边界 |

## 已修复 bug

1. **`deps.py` 会话过期判断**  
   原逻辑对 `datetime` 恒为真，且 naive/aware 比较不安全 → 统一 aware UTC 再比较。

2. **`billing_engine.reserve_credits` 返回类型**  
   零金额返回 `None` 却标注必有 `CreditLedger` → 改为 `Optional[CreditLedger]`。

3. **客户包夹带 `scripts/test_gateway.py`**  
   含假 `sk-` 样例，逆向扫描易误判/噪音 → protect + deb rsync 均排除测试脚本。

4. **`run.sh backup/restore`**  
   - backup 变量遮蔽、打包失败未 `die`  
   - restore 校验备份内容、失败尽量回滚、清理 trap 不破坏全局 ERR  

5. **保护 `__init__.py` 注入**  
   避免掏空包说明；仅追加可选 `_wm` import。

## 非 bug（审计方法注意）

在仓库根目录执行 `PYTHONPATH=/path/to/install python -c "import gateway"` 时，**cwd 优先于 PYTHONPATH**，会加载开发树而非 deb 树。验证客户包请：

```bash
cd /tmp && PYTHONPATH=/path/to/usr/share/ai-gateway-matrix python3 -c "from gateway._wm import agm_wm_probe; print(agm_wm_probe())"
```

## 未改动的已知基线

全量旧测试仍有 **7 个基线失败**（分类器/质检/config_editor，改造前即存在），本次未删测、未伪造成功。
