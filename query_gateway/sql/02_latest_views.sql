






CREATE VIEW IF NOT EXISTS sales_dw.v_dim_region AS
SELECT
    region_id,
    argMax(region_name, _version) AS region_name,
    argMax(province, _version)    AS province,
    argMax(city, _version)        AS city,
    argMax(city_tier, _version)   AS city_tier
FROM sales_dw.dim_region
GROUP BY region_id;

CREATE VIEW IF NOT EXISTS sales_dw.v_dim_customer AS
SELECT
    customer_id,
    argMax(customer_name, _version)  AS customer_name,
    argMax(gender, _version)         AS gender,
    argMax(age, _version)            AS age,
    argMax(customer_level, _version) AS customer_level,
    argMax(register_date, _version)  AS register_date,
    argMax(region_id, _version)      AS region_id
FROM sales_dw.dim_customer
GROUP BY customer_id;

CREATE VIEW IF NOT EXISTS sales_dw.v_dim_product AS
SELECT
    product_id,
    argMax(product_name, _version)  AS product_name,
    argMax(category, _version)      AS category,
    argMax(sub_category, _version)  AS sub_category,
    argMax(brand, _version)         AS brand,
    argMax(standard_cost, _version) AS standard_cost,
    argMax(list_price, _version)    AS list_price
FROM sales_dw.dim_product
GROUP BY product_id;

CREATE VIEW IF NOT EXISTS sales_dw.v_fact_sales_order_line AS
SELECT
    order_id,
    line_no,
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
GROUP BY order_id, line_no;
