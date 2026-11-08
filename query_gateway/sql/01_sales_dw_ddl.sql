








CREATE DATABASE IF NOT EXISTS sales_dw;




CREATE TABLE IF NOT EXISTS sales_dw.dim_region
(
    region_id   UInt32                COMMENT '地区ID',
    region_name LowCardinality(String) COMMENT '大区：华东/华北/华南/华中/西南/西北/东北',
    province    LowCardinality(String) COMMENT '省份',
    city        String                COMMENT '城市',
    city_tier   LowCardinality(String) COMMENT '城市分级：一线/新一线/二线/三线及以下',
    _version    UInt64                COMMENT 'CDC 版本号（写入时间戳），ReplacingMergeTree 版本列'
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY region_id
COMMENT '地区维度表';




CREATE TABLE IF NOT EXISTS sales_dw.dim_customer
(
    customer_id    UInt32                COMMENT '客户ID',
    customer_name  String                COMMENT '客户姓名',
    gender         LowCardinality(String) COMMENT '性别：男/女',
    age            UInt8                 COMMENT '年龄',
    customer_level LowCardinality(String) COMMENT '会员等级：普通/银卡/金卡/钻石',
    register_date  Date                  COMMENT '注册日期',
    region_id      UInt32                COMMENT '常住地区ID（关联 dim_region）',
    _version       UInt64                COMMENT 'CDC 版本号'
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY customer_id
COMMENT '客户维度表';




CREATE TABLE IF NOT EXISTS sales_dw.dim_product
(
    product_id    UInt32                COMMENT '产品ID',
    product_name  String                COMMENT '产品名称',
    category      LowCardinality(String) COMMENT '产品大类',
    sub_category  LowCardinality(String) COMMENT '产品子类',
    brand         LowCardinality(String) COMMENT '品牌',
    standard_cost Decimal(18, 2)        COMMENT '单位标准成本（元）',
    list_price    Decimal(18, 2)        COMMENT '目录标价（元）',
    _version      UInt64                COMMENT 'CDC 版本号'
)
ENGINE = ReplacingMergeTree(_version)
ORDER BY product_id
COMMENT '产品维度表';






CREATE TABLE IF NOT EXISTS sales_dw.fact_sales_order_line
(
    order_id      UInt64                COMMENT '订单ID',
    line_no       UInt8                 COMMENT '订单内行号（一单多行）',
    order_date    Date                  COMMENT '下单日期',
    order_time    DateTime              COMMENT '下单时间',
    customer_id   UInt32                COMMENT '客户ID（关联 dim_customer）',
    product_id    UInt32                COMMENT '产品ID（关联 dim_product）',
    region_id     UInt32                COMMENT '成交门店/收货地区ID（关联 dim_region）',
    channel       LowCardinality(String) COMMENT '销售渠道：线上商城/线下门店/分销',
    sales_rep     String                COMMENT '销售员姓名（线下门店有值，其余为空串）',
    pay_method    LowCardinality(String) COMMENT '支付方式：微信支付/支付宝/银行卡/现金/对公转账',
    quantity      UInt32                COMMENT '购买数量',
    unit_price    Decimal(18, 2)        COMMENT '成交单价（元，折前）',
    discount_rate Decimal(5, 2)         COMMENT '折扣率：0.85 表示八五折，1.00 为原价',
    sales_amount  Decimal(18, 2)        COMMENT '成交金额 = quantity * unit_price * discount_rate',
    cost_amount   Decimal(18, 2)        COMMENT '销售成本 = quantity * standard_cost',
    profit_amount Decimal(18, 2)        COMMENT '毛利 = sales_amount - cost_amount',
    order_status  LowCardinality(String) COMMENT '状态：completed 成交 / returned 退货 / cancelled 取消',
    _version      UInt64                COMMENT 'CDC 版本号；同一(order_id,line_no)多版本取 argMax'
)
ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, order_id, line_no)
SETTINGS index_granularity = 8192
COMMENT '销售订单明细事实表（多版本，查询需 argMax 去重）';
