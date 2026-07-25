# AI Gateway Matrix — 受保护交付构建（对齐 AUTO-R）

客户安装的 **deb 不得** 直接等于开发者源码树（带中文设计注释、完整模块说明、测试夹具）。

## 与 AUTO-R 的对应关系

| AUTO-R | 本项目 |
|--------|--------|
| 去 `#` 注释 / 中文设计说明 | 去 Python 注释与模块 docstring（可配置） |
| 不透明文件名 `mNN_xxx.R` | Python 保留 **import 路径**（否则 import 全断）；用 **字节码编译 + 删除 .py 可选** 增加摩擦 |
| `aaa_wm.R` 水印 + 诱饵 | `gateway/_wm.py` 水印 + 诱饵路由/归一化 |
| `watermark_rules.json` 仅授权人 | 同名规则文件 **禁止进 deb** |
| Minisign + age 设备绑定授权 | 已有 `licensing/`（正式包强制公钥） |
| deb 无开发 `R/` 树 | deb 只装 `build/protected/payload` 或等价受保护树 |

**防护 ≠ 不可破解。** 目标：提高「解包即读业务 + AI 一键复述」的成本。

## 零成本约束

- **不**付费、不订阅、不依赖商业壳。
- **不**修改仓库内开发源码；只写 `build/protected/`（gitignore）。
- 日常 `run.sh` / pytest **不**走保护管线。
- 客户交付推荐：`bash packaging/release-deb.sh`（内部 `AGM_PROTECT=1`）。

## 构建

```bash
# 推荐：客户交付一条龙
bash packaging/release-deb.sh

# 仅生成受保护载荷（开发树不动）
bash packaging/protect/build_protected_package.sh

# 强制保护进 deb
AGM_PROTECT=1 bash packaging/build-deb.sh

# 明文 deb（自用/调试，默认）
bash packaging/build-deb.sh
# 或有公钥时关掉保护：
AGM_PROTECT=0 bash packaging/build-deb.sh
```

可选环境变量：

| 变量 | 含义 |
|------|------|
| `AGM_PROTECT` | `1` 强制保护 / `0` 强制明文 / 默认=仅「有公钥的正式包」自动保护 |
| `AGM_PROTECT_BYTECODE` | 默认 `0`（**保持关闭**，避免运行时成本） |
| `AGM_BUILD_ID` | 固定构建 ID（默认时间戳+随机） |

## 授权人侧验水印

```bash
python3 packaging/protect/verify_watermark.py build/protected
# 或解包后:
python3 packaging/protect/verify_watermark.py /usr/share/ai-gateway-matrix
```

`watermark_rules.json` **只留在你的开发机 / 签发机**，永不进客户 deb。

## 开发

本地源码布局仍用仓库内明文 `gateway/`、`dashboard/`。  
不要把 `build/protected/` 当主开发树提交（可 gitignore）。

## 与「一键保留用户数据」的关系

保护管线只动 **程序树**（`/usr/share/...`）。  
用户 Key/配置仍在 `~/.config/ai-gateway-matrix`，升级 deb 与 `backup`/`restore` 行为不变。
