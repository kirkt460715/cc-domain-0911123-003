# 受权数据计划

维护字段级授权、派生配方和依赖计划，支持本地数据预览、成品证明、受权交付和撤权检查。预览连接采用等长位置对齐。

Python 3.11 标准库与 SQLite 即可运行。数据、时钟和文件均在本地，不需要服务凭据。示例只使用合成记录。

## 关联规则成品

`analysis.run` 从已批准的数据计划生成关联规则分析成品。分析人在请求中固定交易键、物料键、交易日期、发现期、更晚的验证期、最小支持度、最大规则长度和最少共同交易数；字段映射与源数据版本必须落在计划选定的列与钉住的版本上，并沿用计划的用途、区域、字段权利与义务检查。

篮子构建口径：同一交易内同一物料只计一次，重复明细计入折叠统计而不会抬高置信度；空交易键行无法归属交易，单列剔除并计数；退货行在篮子构建前剔除，纯退货交易仍以空篮子身份留在选定总体；缺物料行保留交易身份形成空篮子；属于选定总体的空篮子进入支持度分母。发现期挖掘闭合频繁项集并产生前件、后件非空且互斥的规则，输出支持度、置信度、lift、leverage 和实际四格计数。候选只在发现期产生，验证期对冻结候选重算同一指标并做单侧 Fisher 精确检验，再对整个候选族执行一次 Benjamini–Hochberg 调整；无法计算的规则保留原因，不会先丢弃不显著规则再校正。所有指标以定点十进制字符串给出，相同输入的排序与边界判定稳定；规模超过声明限制时明确拒绝，不会悄悄截断交易。

成品只包含聚合规则与授权谱系，交易明细不随报告交付。撤权、资产退役或计划失鲜会让既有成品立即无法取回或再交付，台账记录仍然保留。

## 运行

在仓库根目录执行：

```sh
python -m rights_graph --db :memory: demo --fixture fixtures/demo.json
python -m rights_graph --db :memory: association-demo --fixture fixtures/association_demo.json
python -m examples.context
python -m examples.association
python -m unittest discover -s tests -v
python -m compileall -q rights_graph examples
```

示例使用内存或临时目录，不会改动持久台账。`examples/context.json` 记录一组可重复导入的业务输入，字段与时间口径见 `examples/数据说明.md`。`examples.context` 检查数据并调用现有产品入口；它只展示原有业务数据，不计算分析结论。`examples.association` 与 `fixtures/association_demo.json` 演示重复明细折叠、空篮子进分母、一个在发现期 lift=4 但验证期共同计数归零的组合，以及撤权后成品不可继续取得的行为。

`tests` 覆盖现有业务生命周期、非法输入、版本和完整性边界，另有示例导入回归。完整测试和上述演示可在一次短终端会话中执行。

## 容器

```sh
docker build -t local/rights_graph .
docker run --rm --network none local/rights_graph
docker run --rm --network none local/rights_graph python -m examples.context
```

镜像构建完成后运行和测试不下载文件。持久化使用各自命令的本地路径参数；具体操作及参数可通过 `python -m rights_graph --help` 查询。第三方依赖清单为空。
