# 验签公钥目录

在授权人可信电脑上执行一次：

```bash
bash licensing/init_issuer.sh
```

会生成密码保护的签发私钥（默认在 `~/.local/share/ai-gateway-issuer/`，**不进仓库**），
并把对应公钥写到本目录的 `ai-gateway.pub`。

- 正式 Debian 包在缺少 `ai-gateway.pub` 时拒绝构建。
- 私钥不得进入本仓库、deb 包或客户机。
