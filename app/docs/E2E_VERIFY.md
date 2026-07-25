# 重组后真实检验报告

日期：2026-07-24  
布局：`README.md` / `run.sh` / `backup.sh` / `jiyi.txt` / `app/` / `home/`

## 结果总表

| 检查 | 结果 |
|------|------|
| 根目录布局 | PASS |
| `run.sh home` → `…/home` | PASS |
| `run.sh jiyi path` → 仓库根 `jiyi.txt` | PASS |
| Python AST（app 下） | 316 文件 / 0 错误 |
| bash -n | PASS |
| pytest billing + protect + run_script | **40 passed** |
| `python -m scripts.validate_config` | **严格配置校验通过**（已修 DATA_ROOT→home） |
| `run.sh start` Docker 全服务 healthy | PASS |
| `GET /healthz` | `{"status":"ok"}` |
| `GET /`、`/console/` | HTTP 200 |
| `GET /api/v1/system/health` | postgres+redis true |
| `GET /v1/models`（master key） | 返回 auto-route 等模型 |
| jiyi save → 删文件 → load 恢复 | PASS |

## 过程中修复

1. **validate_config / health_check / create_client_key** 默认数据目录改为识别 `repo/home`（重组后若仍用 `app/` 当数据根会误报挂载路径不存在）。
2. **启动前**若残留旧容器名冲突，需 `docker rm -f` 旧实例（一次性环境问题，非代码逻辑）。

## 结论

重组后项目可正常启动、鉴权入口可用、OpenAI 模型列表可读、记忆文件 jiyi 往返正常。  
服务仍在运行时可用 `./run.sh status` / `./run.sh stop` 管理。
