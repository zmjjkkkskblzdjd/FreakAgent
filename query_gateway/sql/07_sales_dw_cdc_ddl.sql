























CREATE DATABASE IF NOT EXISTS sales_dw;


CREATE TABLE IF NOT EXISTS sales_dw.dim_region
(
    region_id   UInt32, region_name LowCardinality(String),
    province    LowCardinality(String), city String,
    city_tier   LowCardinality(String),
    _version    UInt64, is_deleted UInt8 DEFAULT 0
) ENGINE = ReplacingMergeTree(_version) ORDER BY region_id;

CREATE TABLE IF NOT EXISTS sales_dw.dim_product
(
    product_id    UInt32, product_name String,
    category      LowCardinality(String), sub_category LowCardinality(String),
    brand         LowCardinality(String),
    standard_cost Decimal(18, 2), list_price Decimal(18, 2),
    _version      UInt64, is_deleted UInt8 DEFAULT 0
) ENGINE = ReplacingMergeTree(_version) ORDER BY product_id;

CREATE TABLE IF NOT EXISTS sales_dw.dim_customer
(
    customer_id    UInt32, customer_name String,
    gender         LowCardinality(String), age UInt8,
    customer_level LowCardinality(String), register_date Date,
    region_id      UInt32,
    _version       UInt64, is_deleted UInt8 DEFAULT 0
) ENGINE = ReplacingMergeTree(_version) ORDER BY customer_id;

CREATE TABLE IF NOT EXISTS sales_dw.fact_sales_order_line
(
    order_id      UInt64, line_no UInt8,
    order_date    Date, order_time DateTime,
    customer_id   UInt32, product_id UInt32, region_id UInt32,
    channel       LowCardinality(String), sales_rep String,
    pay_method    LowCardinality(String),
    quantity      UInt32,
    unit_price    Decimal(18, 2), discount_rate Decimal(5, 2),
    sales_amount  Decimal(18, 2), cost_amount Decimal(18, 2),
    profit_amount Decimal(18, 2),
    order_status  LowCardinality(String),
    _version      UInt64, is_deleted UInt8 DEFAULT 0
) ENGINE = ReplacingMergeTree(_version)
PARTITION BY toYYYYMM(order_date)
ORDER BY (order_date, order_id, line_no)
TTL order_date + INTERVAL 5 YEAR DELETE
SETTINGS index_granularity = 8192;


CREATE TABLE IF NOT EXISTS sales_dw.kafka_dim_region (raw String)
ENGINE = Kafka SETTINGS
    kafka_broker_list = '192.168.1.135:9092',
    kafka_topic_list = 'salesbiz.sales_biz.dim_region',
    kafka_group_name = 'ch_sales_dw_dim_region',
    kafka_format = 'JSONAsString', kafka_num_consumers = 1;

CREATE TABLE IF NOT EXISTS sales_dw.kafka_dim_product (raw String)
ENGINE = Kafka SETTINGS
    kafka_broker_list = '192.168.1.135:9092',
    kafka_topic_list = 'salesbiz.sales_biz.dim_product',
    kafka_group_name = 'ch_sales_dw_dim_product',
    kafka_format = 'JSONAsString', kafka_num_consumers = 1;

CREATE TABLE IF NOT EXISTS sales_dw.kafka_dim_customer (raw String)
ENGINE = Kafka SETTINGS
    kafka_broker_list = '192.168.1.135:9092',
    kafka_topic_list = 'salesbiz.sales_biz.dim_customer',
    kafka_group_name = 'ch_sales_dw_dim_customer',
    kafka_format = 'JSONAsString', kafka_num_consumers = 1;

CREATE TABLE IF NOT EXISTS sales_dw.kafka_fact_sales_order_line (raw String)
ENGINE = Kafka SETTINGS
    kafka_broker_list = '192.168.1.135:9092',
    kafka_topic_list = 'salesbiz.sales_biz.fact_sales_order_line',
    kafka_group_name = 'ch_sales_dw_fact_sales_order_line',
    kafka_format = 'JSONAsString', kafka_num_consumers = 1;



CREATE MATERIALIZED VIEW IF NOT EXISTS sales_dw.mv_dim_region TO sales_dw.dim_region AS
WITH multiIf(JSONExtract(raw, 'op', 'String') = 'd',
             JSONExtractRaw(raw, 'before'),
             JSONExtractRaw(raw, 'after')) AS payload
SELECT
    JSONExtract(payload, 'region_id', 'UInt32')   AS region_id,
    JSONExtract(payload, 'region_name', 'String') AS region_name,
    JSONExtract(payload, 'province', 'String')    AS province,
    JSONExtract(payload, 'city', 'String')        AS city,
    JSONExtract(payload, 'city_tier', 'String')   AS city_tier,
    JSONExtract(raw, 'ts_ms', 'UInt64')           AS _version,
    multiIf(JSONExtract(raw, 'op', 'String') = 'd', toUInt8(1), toUInt8(0)) AS is_deleted
FROM sales_dw.kafka_dim_region
WHERE JSONExtract(raw, 'op', 'String') IN ('c', 'u', 'r', 'd');






CREATE MATERIALIZED VIEW IF NOT EXISTS sales_dw.mv_dim_product TO sales_dw.dim_product AS
WITH multiIf(JSONExtract(raw, 'op', 'String') = 'd',
             JSONExtractRaw(raw, 'before'),
             JSONExtractRaw(raw, 'after')) AS payload
SELECT
    JSONExtract(payload, 'product_id', 'UInt32')                  AS product_id,
    JSONExtract(payload, 'product_name', 'String')                AS product_name,
    JSONExtract(payload, 'category', 'String')                    AS category,
    JSONExtract(payload, 'sub_category', 'String')                AS sub_category,
    JSONExtract(payload, 'brand', 'String')                       AS brand,
    toDecimal64(JSONExtract(payload, 'standard_cost', 'String'), 2) AS standard_cost,
    toDecimal64(JSONExtract(payload, 'list_price', 'String'), 2)    AS list_price,
    JSONExtract(raw, 'ts_ms', 'UInt64')                           AS _version,
    multiIf(JSONExtract(raw, 'op', 'String') = 'd', toUInt8(1), toUInt8(0)) AS is_deleted
FROM sales_dw.kafka_dim_product
WHERE JSONExtract(raw, 'op', 'String') IN ('c', 'u', 'r', 'd');


CREATE MATERIALIZED VIEW IF NOT EXISTS sales_dw.mv_dim_customer TO sales_dw.dim_customer AS
WITH multiIf(JSONExtract(raw, 'op', 'String') = 'd',
             JSONExtractRaw(raw, 'before'),
             JSONExtractRaw(raw, 'after')) AS payload
SELECT
    JSONExtract(payload, 'customer_id', 'UInt32')          AS customer_id,
    JSONExtract(payload, 'customer_name', 'String')        AS customer_name,
    JSONExtract(payload, 'gender', 'String')               AS gender,
    JSONExtract(payload, 'age', 'UInt8')                   AS age,
    JSONExtract(payload, 'customer_level', 'String')       AS customer_level,
    toDate(JSONExtract(payload, 'register_date', 'Int32')) AS register_date,
    JSONExtract(payload, 'region_id', 'UInt32')            AS region_id,
    JSONExtract(raw, 'ts_ms', 'UInt64')                    AS _version,
    multiIf(JSONExtract(raw, 'op', 'String') = 'd', toUInt8(1), toUInt8(0)) AS is_deleted
FROM sales_dw.kafka_dim_customer
WHERE JSONExtract(raw, 'op', 'String') IN ('c', 'u', 'r', 'd');


CREATE MATERIALIZED VIEW IF NOT EXISTS sales_dw.mv_fact_sales_order_line TO sales_dw.fact_sales_order_line AS
WITH multiIf(JSONExtract(raw, 'op', 'String') = 'd',
             JSONExtractRaw(raw, 'before'),
             JSONExtractRaw(raw, 'after')) AS payload
SELECT
    JSONExtract(payload, 'order_id', 'UInt64')               AS order_id,
    JSONExtract(payload, 'line_no', 'UInt8')                 AS line_no,
    toDate(JSONExtract(payload, 'order_date', 'Int32'))      AS order_date,
    
    
    
    toDateTime(intDiv(JSONExtract(payload, 'order_time', 'Int64'), 1000) - 28800) AS order_time,
    JSONExtract(payload, 'customer_id', 'UInt32')            AS customer_id,
    JSONExtract(payload, 'product_id', 'UInt32')             AS product_id,
    JSONExtract(payload, 'region_id', 'UInt32')              AS region_id,
    JSONExtract(payload, 'channel', 'String')                AS channel,
    JSONExtract(payload, 'sales_rep', 'String')              AS sales_rep,
    JSONExtract(payload, 'pay_method', 'String')             AS pay_method,
    JSONExtract(payload, 'quantity', 'UInt32')               AS quantity,
    toDecimal64(JSONExtract(payload, 'unit_price', 'String'), 2)    AS unit_price,
    toDecimal32(JSONExtract(payload, 'discount_rate', 'String'), 2) AS discount_rate,
    toDecimal64(JSONExtract(payload, 'sales_amount', 'String'), 2)  AS sales_amount,
    toDecimal64(JSONExtract(payload, 'cost_amount', 'String'), 2)   AS cost_amount,
    toDecimal64(JSONExtract(payload, 'profit_amount', 'String'), 2) AS profit_amount,
    JSONExtract(payload, 'order_status', 'String')           AS order_status,
    JSONExtract(raw, 'ts_ms', 'UInt64')                      AS _version,
    multiIf(JSONExtract(raw, 'op', 'String') = 'd', toUInt8(1), toUInt8(0)) AS is_deleted
FROM sales_dw.kafka_fact_sales_order_line
WHERE JSONExtract(raw, 'op', 'String') IN ('c', 'u', 'r', 'd');


CREATE VIEW IF NOT EXISTS sales_dw.v_dim_region AS
SELECT region_id,
       argMax(region_name, _version) AS region_name,
       argMax(province, _version)    AS province,
       argMax(city, _version)        AS city,
       argMax(city_tier, _version)   AS city_tier
FROM sales_dw.dim_region
GROUP BY region_id
HAVING argMax(is_deleted, _version) = 0;

CREATE VIEW IF NOT EXISTS sales_dw.v_dim_product AS
SELECT product_id,
       argMax(product_name, _version)  AS product_name,
       argMax(category, _version)      AS category,
       argMax(sub_category, _version)  AS sub_category,
       argMax(brand, _version)         AS brand,
       argMax(standard_cost, _version) AS standard_cost,
       argMax(list_price, _version)    AS list_price
FROM sales_dw.dim_product
GROUP BY product_id
HAVING argMax(is_deleted, _version) = 0;

CREATE VIEW IF NOT EXISTS sales_dw.v_dim_customer AS
SELECT customer_id,
       argMax(customer_name, _version)  AS customer_name,
       argMax(gender, _version)         AS gender,
       argMax(age, _version)            AS age,
       argMax(customer_level, _version) AS customer_level,
       argMax(register_date, _version)  AS register_date,
       argMax(region_id, _version)      AS region_id
FROM sales_dw.dim_customer
GROUP BY customer_id
HAVING argMax(is_deleted, _version) = 0;

CREATE VIEW IF NOT EXISTS sales_dw.v_fact_sales_order_line AS
SELECT order_id, line_no,
       argMax(order_date, _version)    AS order_date,
       argMax(order_time, _version)    AS order_time,
       argMax(customer_id, _version)   AS customer_id,
       argMax(product_id, _version)    AS product_id,
       argMax(region_id, _version)     AS region_id,
       argMax(channel, _version)       AS channel,
       argMax(sales_rep, _version)     AS sales_rep,
       argMax(pay_method, _version)    AS pay_method,
       argMax(quantity, _version)      AS quantity,
       argMax(unit_price, _version)    AS unit_price,
       argMax(discount_rate, _version) AS discount_rate,
       argMax(sales_amount, _version)  AS sales_amount,
       argMax(cost_amount, _version)   AS cost_amount,
       argMax(profit_amount, _version) AS profit_amount,
       argMax(order_status, _version)  AS order_status
FROM sales_dw.fact_sales_order_line
GROUP BY order_id, line_no
HAVING argMax(is_deleted, _version) = 0;
