# 特征化快照基线（S 片，ADR-0021 决定 2/5）

本目录是分发脚本外部行为的逐字基线。生成它的测试在 `tests/test_snapshot_routes.py`
与 `tests/test_snapshot_contracts.py`，缝是 `harness.run` ＋ `FakeHerdr`；每个场景
一份 JSON，记录交付四面与过程面：

- `rc`、归一化后的 `stdout` 原文、`.result.json` 原文、`.notify.json` 原文；
- 终态台账 `ledger_final`；
- `phase_sequence`（命令收口处观察到的 phase 转移＋终态 phase）；
- `commands`：每条命令到达时读一次磁盘台账（`phase` 与相对上一条命令的 `changes`），
  用来钉住「先记账后发送」的次序（ADR-0022 决定 5）。

临时目录路径归一为 `<TMP>`、ISO 时间戳归一为 `<TS>`；其余字段逐字比对。
本目录只放占位符后的假数据（公开仓库隐私红线：不含本机路径、用户名、主机名、
真实 token）。

## 运行

```sh
cd tests
python3 test_snapshot_routes.py        # 与基线逐字比对
python3 test_snapshot_contracts.py
```

## 更新规则

- **重构片（G2/G3/P1/P2/P3/P4、别名退役、终态）快照 diff 必须为空，不许更新。**
  任何非零 diff 都按回归处理，先修代码。
- **只有行为性 bugfix 才允许改基线**：须在 PR 里显式列出 diff，并写明是有意的
  契约变化。
- **#113 串行链的唯一例外是 ④journal 片**：约 5 处延后落盘改为立即落盘只改中间态、
  不改终态与 phase 次序；该 commit 的 PR 段落须列出快照 diff 并写明「有意的契约
  变化」（ADR-0022 决定 5）。
- 新增功能需要新场景时，用 `python3 <测试文件> --update` 生成；`--update` 不会在
  普通运行（含 CI）里发生。
