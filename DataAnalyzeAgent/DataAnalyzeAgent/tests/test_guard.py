
import pytest

from config import get_app_config
from data_analyze_agent.core import ColumnMeta, TableMeta
from data_analyze_agent.db.guard import check

_LIMIT = get_app_config().sql_guard.inject_limit   



def _meta(table: str, comment: str, cols: list[tuple]) -> TableMeta:
    return TableMeta(
        table_name=table,
        table_comment=comment,
        ddl=f"{table}({', '.join(n for n, _t, _c in cols)})",
        columns=[
            ColumnMeta(name=n, data_type=t, is_nullable=False,
                       column_key="", column_comment=c)
            for n, t, c in cols
        ],
        schema_hash="fake-hash",
    )


SCHEMA_CACHE = [
    _meta("v_fact_sales_order_line", "销售订单明细（最新版本视图）", [
        ("order_id", "UInt64", "订单ID"),
        ("line_no", "UInt8", "订单内行号"),
        ("order_date", "Date", "下单日期"),
        ("order_time", "DateTime", "下单时间"),
        ("customer_id", "UInt32", "客户ID"),
        ("product_id", "UInt32", "产品ID"),
        ("region_id", "UInt32", "地区ID"),
        ("channel", "LowCardinality(String)", "销售渠道：线上商城/线下门店/分销"),
        ("pay_method", "LowCardinality(String)", "支付方式：微信支付/支付宝/银行卡/现金/对公转账"),
        ("quantity", "UInt32", "购买数量"),
        ("unit_price", "Decimal(18, 2)", "成交单价"),
        ("sales_amount", "Decimal(18, 2)", "成交金额"),
        ("cost_amount", "Decimal(18, 2)", "销售成本"),
        ("profit_amount", "Decimal(18, 2)", "毛利"),
        ("order_status", "LowCardinality(String)", "状态：completed 成交/returned 退货/cancelled 取消"),
    ]),
    _meta("v_dim_customer", "客户维度（最新版本视图）", [
        ("customer_id", "UInt32", "客户ID"),
        ("customer_name", "String", "客户姓名"),
        ("gender", "LowCardinality(String)", "性别：男/女"),
        ("age", "UInt8", "年龄"),
        ("customer_level", "LowCardinality(String)", "会员等级：普通/银卡/金卡/钻石"),
        ("register_date", "Date", "注册日期"),
        ("region_id", "UInt32", "常住地区ID"),
    ]),
    _meta("v_dim_region", "地区维度（最新版本视图）", [
        ("region_id", "UInt32", "地区ID"),
        ("region_name", "LowCardinality(String)", "大区：华东/华北/华南/华中/西南/西北/东北"),
        ("province", "LowCardinality(String)", "省份"),
        ("city", "String", "城市"),
        ("city_tier", "LowCardinality(String)", "城市分级：一线/新一线/二线/三线及以下"),
    ]),
    _meta("fact_sales_order_line", "销售订单明细（物理表）", [
        ("order_id", "UInt64", "订单ID"),
        ("line_no", "UInt8", "订单内行号"),
        ("order_date", "Date", "下单日期"),
        ("customer_id", "UInt32", "客户ID"),
        ("product_id", "UInt32", "产品ID"),
        ("region_id", "UInt32", "地区ID"),
        ("quantity", "UInt32", "购买数量"),
        ("unit_price", "Decimal(18, 2)", "成交单价"),
        ("sales_amount", "Decimal(18, 2)", "成交金额"),
        ("order_status", "LowCardinality(String)", "状态：completed 成交/returned 退货/cancelled 取消"),
    ]),
    _meta("dim_region", "地区维度（物理表）", [
        ("region_id", "UInt32", "地区ID"),
        ("region_name", "LowCardinality(String)", "大区：华东/华北/华南/华中/西南/西北/东北"),
        ("province", "LowCardinality(String)", "省份"),
        ("city", "String", "城市"),
    ]),
    _meta("dim_customer", "客户维度（物理表）", [
        ("customer_id", "UInt32", "客户ID"),
        ("customer_name", "String", "客户姓名"),
        ("gender", "LowCardinality(String)", "性别：男/女"),
        ("age", "UInt8", "年龄"),
        ("customer_level", "LowCardinality(String)", "会员等级：普通/银卡/金卡/钻石"),
        ("region_id", "UInt32", "常住地区ID"),
    ]),
]


EVIL_SQL = [
    ("drop_table",       "DROP TABLE v_dim_customer",                      "not_read_only"),
    ("truncate",         "TRUNCATE TABLE v_dim_customer",                  "not_read_only"),
    ("delete_all",       "ALTER TABLE v_dim_customer DELETE WHERE 1=1",    "not_read_only"),
    ("update_in_ask",    "ALTER TABLE v_fact_sales_order_line UPDATE order_status = 'cancelled' WHERE order_id = 1",
                                                                           "not_read_only"),
    ("insert_in_ask",    "INSERT INTO v_dim_customer (customer_id) VALUES (1)",
                                                                           "not_read_only"),
    ("multi_statement",  "SELECT 1; DROP TABLE v_dim_customer",            "multi_statement"),
    ("sleep",            "SELECT sleep(5)",                                "forbidden_function"),
    ("sleep_each_row",   "SELECT * FROM v_dim_customer WHERE sleepEachRow(1) = 0",
                                                                           "forbidden_function"),
    ("joinget",          "SELECT joinGet('d', 'v', customer_id) FROM v_dim_customer",
                                                                           "forbidden_function"),
    ("current_user",     "SELECT currentUser()",                           "forbidden_function"),
    ("version",          "SELECT version()",                               "forbidden_function"),
    ("empty_sql",        "",                                               "other"),
    ("not_sql",          "THIS IS NOT SQL AT ALL",                         "other"),
    ("tf_remote",        "SELECT * FROM remote('h', sales_dw, v_dim_customer)",
                                                                           "forbidden_table_function"),
    ("tf_url",           "SELECT * FROM url('http://evil/x.csv', 'CSV', 'x UInt32')",
                                                                           "forbidden_table_function"),
    ("tf_file",          "SELECT * FROM file('data.csv', 'CSV', 'x UInt32')",
                                                                           "forbidden_table_function"),
    ("tf_s3",            "SELECT * FROM s3('http://b/k.csv', 'CSV', 'x UInt32')",
                                                                           "forbidden_table_function"),
    ("tf_mysql",         "SELECT * FROM mysql('h:3306', 'db', 't', 'u', 'p')",
                                                                           "forbidden_table_function"),
    ("tf_numbers",       "SELECT * FROM numbers(1000000000)",              "forbidden_table_function"),
    ("tf_cluster",       "SELECT * FROM cluster('c', sales_dw, v_dim_customer)",
                                                                           "forbidden_table_function"),
    ("unknown_table",    "SELECT * FROM inventory",                        "unknown_table"),
    ("probe_system",     "SELECT * FROM system.tables",                    "unknown_table"),
    ("probe_information_schema",
                         "SELECT * FROM information_schema.tables",        "unknown_table"),
    ("wrong_db_prefix",  "SELECT * FROM other_db.v_dim_customer",          "unknown_table"),
    ("union_meta_probe", "SELECT order_id FROM v_fact_sales_order_line UNION ALL SELECT table_name FROM system.tables",
                                                                           "unknown_table"),
    ("unknown_column",   "SELECT statu FROM v_dim_customer",               "unknown_column"),
    ("unknown_col_qual", "SELECT v_dim_customer.statu FROM v_dim_customer", "unknown_column"),
]

GOOD_SQL = [
    "SELECT order_id, order_status FROM v_fact_sales_order_line WHERE order_id = 1 LIMIT 10",
    "SELECT order_id FROM sales_dw.v_fact_sales_order_line WHERE order_id = 1 LIMIT 10",
    "SELECT f.order_id, c.customer_name FROM v_fact_sales_order_line f "
    "JOIN v_dim_customer c ON f.customer_id = c.customer_id "
    "WHERE c.customer_level = '金卡' LIMIT 20",
    "SELECT toYYYYMM(order_date) AS month, uniqExact(order_id) AS orders "
    "FROM v_fact_sales_order_line WHERE order_status = 'completed' "
    "GROUP BY month ORDER BY month LIMIT 24",
    "SELECT sum(sales_amount) AS gmv FROM v_fact_sales_order_line "
    "WHERE order_status = 'completed' AND order_date >= '2025-01-01' AND order_date < '2025-02-01'",
    "SELECT order_id, argMax(order_status, _v) AS st FROM "
    "(SELECT order_id, order_status, toUInt64(1) AS _v FROM v_fact_sales_order_line) "
    "GROUP BY order_id LIMIT 10",
    "SELECT uniqExactIf(order_id, order_status = 'returned') AS returned_orders "
    "FROM v_fact_sales_order_line",
    "SELECT * FROM v_fact_sales_order_line WHERE customer_id IN "
    "(SELECT customer_id FROM v_dim_customer WHERE customer_level = '钻石') LIMIT 10",
    "WITH t AS (SELECT order_id, sales_amount FROM v_fact_sales_order_line) "
    "SELECT order_id FROM t LIMIT 5",
    "WITH t AS (SELECT customer_id, sum(sales_amount) AS amt FROM v_fact_sales_order_line "
    "GROUP BY customer_id) SELECT customer_id FROM t ORDER BY amt DESC LIMIT 5",
    "SELECT r.region_name, sum(f.sales_amount) AS gmv FROM v_fact_sales_order_line f "
    "JOIN v_dim_region r ON f.region_id = r.region_id "
    "WHERE f.order_status = 'completed' GROUP BY r.region_name",
    "SELECT count() FROM v_dim_customer",  
]



@pytest.mark.parametrize(("name", "sql", "code"), EVIL_SQL, ids=[e[0] for e in EVIL_SQL])
def test_evil_sql_blocked(name: str, sql: str, code: str) -> None:
    verdict = check(sql, SCHEMA_CACHE)
    assert not verdict.passed, f"{name} 竟然放行：{verdict}"
    assert any(i.code == code for i in verdict.issues), \
        f"{name} 未命中 {code}，实际 issues：{[(i.code, i.detail) for i in verdict.issues]}"
    for issue in verdict.issues:  
        assert issue.detail.strip(), f"{name} 的 {issue.code} detail 为空"



@pytest.mark.parametrize("sql", GOOD_SQL, ids=range(1, len(GOOD_SQL) + 1))
def test_good_sql_passes(sql: str) -> None:
    verdict = check(sql, SCHEMA_CACHE)
    assert verdict.passed, f"合法 SQL 被误伤：{sql}\nissues：{[(i.code, i.detail) for i in verdict.issues]}"



def test_missing_limit_auto_fixed() -> None:
    verdict = check("SELECT * FROM v_dim_region WHERE region_name = '华东'", SCHEMA_CACHE)
    assert verdict.passed, "LIMIT 注入是零风险改写，不应拒绝"
    issue = next(i for i in verdict.issues if i.code == "missing_limit")
    assert issue.auto_fixed_sql is not None and issue.auto_fixed_sql.endswith(f"LIMIT {_LIMIT}")
    assert verdict.checked_sql == issue.auto_fixed_sql, "checked_sql 应指向改写后的版本"



def test_lane_write_allows_dml_and_bogus_rejected() -> None:
    verdict = check("UPDATE sales_biz.dim_region SET region_name='华东大区' WHERE region_id=1",
                    SCHEMA_CACHE, lane="write")
    assert verdict.passed, f"lane=write 合法 DML 被误伤：{[(i.code, i.detail) for i in verdict.issues]}"
    with pytest.raises(ValueError):
        check("SELECT 1", SCHEMA_CACHE, lane="bogus")


def test_dml_blocked_as_not_read_only() -> None:
    verdict = check(
        "ALTER TABLE v_dim_customer UPDATE customer_level = '金卡' WHERE customer_id = 1",
        SCHEMA_CACHE,
    )
    assert not verdict.passed
    assert any(i.code == "not_read_only" for i in verdict.issues)


def test_schema_cache_required() -> None:
    with pytest.raises(ValueError):
        check("SELECT 1", None)


WRITE_SQL = [
    ("update放行", "UPDATE sales_biz.dim_region SET region_name='华东大区' WHERE region_id=1", None),
    ("delete放行", "DELETE FROM sales_biz.dim_customer WHERE customer_id=42", None),
    ("insert放行", "INSERT INTO sales_biz.dim_region (region_id, region_name) VALUES (91,'华东')", None),
    ("mysql术语insert放行", "INSERT INTO sales_meta.kb_glossary (name, formula) VALUES ('GMV','成交总额')", None),
    ("mysql例题update放行", "UPDATE sales_meta.kb_fewshot SET status='disabled' WHERE id=7", None),
    ("ddl拒", "CREATE TABLE sales_biz.evil (x INT)", "not_write_dml"),
    ("select拒", "SELECT * FROM sales_biz.dim_region", "not_write_dml"),
    ("truncate拒", "TRUNCATE TABLE sales_biz.dim_region", "not_write_dml"),
    ("update无where拒", "UPDATE sales_biz.dim_region SET region_name='x'", "missing_where"),
    ("delete无where拒", "DELETE FROM sales_biz.dim_region", "missing_where"),
    ("写ch视图拒", "UPDATE sales_dw.v_dim_region SET region_name='x' WHERE region_id=1", "write_table_forbidden"),
    ("写kb于ch拒", "INSERT INTO sales_dw.kb_glossary (name) VALUES ('x')", "write_table_forbidden"),
    ("写ch业务表拒", "UPDATE sales_dw.fact_sales_order_line SET quantity=1 WHERE order_id=1", "write_table_forbidden"),
    ("kb_meta锁死", "UPDATE sales_meta.kb_meta SET meta_value='x' WHERE meta_key='domain'", "write_table_forbidden"),
    ("mysql业务表拒", "DELETE FROM sales_meta.orders WHERE id=1", "write_table_forbidden"),
    ("裸表名拒", "UPDATE dim_region SET region_name='x' WHERE region_id=1", "cross_database"),
    ("危险函数拒", "UPDATE sales_biz.dim_region SET region_name=sleep(1) WHERE region_id=1", "forbidden_function"),
    ("多语句拒", "DELETE FROM sales_biz.dim_region WHERE region_id=1; DELETE FROM sales_biz.dim_region WHERE region_id=2", "multi_statement"),
    ("无法解析拒", "UPDAT sales_biz.dim_region SET x=1", "other"),
]


@pytest.mark.parametrize("name,sql,expect_code", WRITE_SQL, ids=[c[0] for c in WRITE_SQL])
def test_write_lane(name, sql, expect_code):
    verdict = check(sql, SCHEMA_CACHE, lane="write")
    if expect_code is None:
        assert verdict.passed, f"{name}: 合法 DML 被误伤 {[(i.code, i.detail) for i in verdict.issues]}"
        assert verdict.checked_sql == sql  
    else:
        assert not verdict.passed, f"{name}: 恶 SQL 未拦截"
        assert expect_code in {i.code for i in verdict.issues}, \
            f"{name}: 期望 {expect_code}，实际 {[i.code for i in verdict.issues]}"


def test_write_lane_bogus_lane():
    with pytest.raises(ValueError):
        check("SELECT 1", SCHEMA_CACHE, lane="bogus")
