








CREATE DATABASE IF NOT EXISTS sales_biz
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE sales_biz;

CREATE TABLE IF NOT EXISTS dim_region (
    region_id   INT UNSIGNED NOT NULL COMMENT '地区ID',
    region_name VARCHAR(32)  NOT NULL COMMENT '大区',
    province    VARCHAR(32)  NOT NULL COMMENT '省份',
    city        VARCHAR(32)  NOT NULL COMMENT '城市',
    city_tier   VARCHAR(16)  NOT NULL COMMENT '城市分级',
    PRIMARY KEY (region_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='地区维度';

CREATE TABLE IF NOT EXISTS dim_product (
    product_id    INT UNSIGNED  NOT NULL COMMENT '产品ID',
    product_name  VARCHAR(128)  NOT NULL COMMENT '产品名称',
    category      VARCHAR(32)   NOT NULL COMMENT '产品大类',
    sub_category  VARCHAR(32)   NOT NULL COMMENT '产品子类',
    brand         VARCHAR(32)   NOT NULL COMMENT '品牌',
    standard_cost DECIMAL(18,2) NOT NULL COMMENT '单位标准成本（元）',
    list_price    DECIMAL(18,2) NOT NULL COMMENT '目录标价（元）',
    PRIMARY KEY (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='产品维度';

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id    INT UNSIGNED     NOT NULL COMMENT '客户ID',
    customer_name  VARCHAR(64)      NOT NULL COMMENT '客户姓名',
    gender         VARCHAR(4)       NOT NULL COMMENT '性别',
    age            TINYINT UNSIGNED NOT NULL COMMENT '年龄',
    customer_level VARCHAR(8)       NOT NULL COMMENT '会员等级',
    register_date  DATE             NOT NULL COMMENT '注册日期',
    region_id      INT UNSIGNED     NOT NULL COMMENT '常住地区ID',
    PRIMARY KEY (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户维度';

CREATE TABLE IF NOT EXISTS fact_sales_order_line (
    order_id      BIGINT UNSIGNED   NOT NULL COMMENT '订单ID',
    line_no       TINYINT UNSIGNED  NOT NULL COMMENT '订单内行号',
    order_date    DATE              NOT NULL COMMENT '下单日期',
    order_time    DATETIME          NOT NULL COMMENT '下单时间',
    customer_id   INT UNSIGNED      NOT NULL COMMENT '客户ID',
    product_id    INT UNSIGNED      NOT NULL COMMENT '产品ID',
    region_id     INT UNSIGNED      NOT NULL COMMENT '成交地区ID',
    channel       VARCHAR(16)       NOT NULL COMMENT '销售渠道',
    sales_rep     VARCHAR(32)       NOT NULL DEFAULT '' COMMENT '销售员姓名',
    pay_method    VARCHAR(16)       NOT NULL COMMENT '支付方式',
    quantity      INT UNSIGNED      NOT NULL COMMENT '购买数量',
    unit_price    DECIMAL(18,2)     NOT NULL COMMENT '成交单价',
    discount_rate DECIMAL(5,2)      NOT NULL COMMENT '折扣率',
    sales_amount  DECIMAL(18,2)     NOT NULL COMMENT '成交金额',
    cost_amount   DECIMAL(18,2)     NOT NULL COMMENT '销售成本',
    profit_amount DECIMAL(18,2)     NOT NULL COMMENT '毛利',
    order_status  VARCHAR(16)       NOT NULL COMMENT 'completed/returned/cancelled',
    PRIMARY KEY (order_id, line_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='销售订单明细事实';
