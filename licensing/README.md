# AI Gateway Matrix 离线 B 端授权

借鉴 AUTO-R：设备申请码 + Minisign/Ed25519 验签 + age/X25519 设备加密。
客户机只有验签公钥；密码保护的签发私钥只在授权人电脑。

## 客户流程

1. 启动失败或打开激活页时，复制设备申请码（`AG1....`）发给授权人。
2. 授权人签发 `.lic` 文件。
3. 客户把 `.lic` 放到桌面，再执行 `ai-gateway-matrix start` / `app`。
4. 软件自动验签、导入到用户数据目录，并删除桌面运输副本。
5. 之后可永久离线使用（绑定本机设备指纹）。

## 授权人：一次性初始化

```bash
sudo apt install minisign age jq
bash licensing/init_issuer.sh
```

私钥默认：`~/.local/share/ai-gateway-issuer/`
公钥写入：`licensing/public/ai-gateway.pub`（进仓库/安装包，**永不**放私钥）。

## 签发

```bash
bash licensing/issue_license.sh
# 或
bash licensing/issue_license.sh 'AG1....' --customer '某某公司'
```

## 开发豁免

| 条件 | 行为 |
|------|------|
| 无 `licensing/public/ai-gateway.pub` | **开发模式**：不强制授权，启动时警告 |
| `AI_GATEWAY_LICENSE_BYPASS=1` | 强制跳过授权（仅本机开发） |
| 有公钥且未 bypass | **不激活不启动** Docker / 桌面控制台 |

正式 deb 构建在缺少公钥时会失败。

## 数据位置

许可证与设备身份在可迁移目录内：

```text
$AI_GATEWAY_HOME/license/
  device-age.key    # 本机 age 身份
  license.lic       # 已导入许可证
```

换机须重新申请码并重新签发（设备指纹绑定）。

## CLI

```bash
ai-gateway-matrix license request
ai-gateway-matrix license status
ai-gateway-matrix license import /path/to/file.lic
ai-gateway-matrix license ensure
ai-gateway-matrix license device-id
```
