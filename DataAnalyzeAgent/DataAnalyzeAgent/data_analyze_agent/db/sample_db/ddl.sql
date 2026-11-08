






CREATE TABLE IF NOT EXISTS customers (
    customer_id    BIGINT       NOT NULL AUTO_INCREMENT COMMENT '客户ID',
    nickname       VARCHAR(50)  NOT NULL COMMENT '昵称',
    gender         VARCHAR(4)   NOT NULL COMMENT '性别：男/女',
    age            TINYINT      NOT NULL COMMENT '年龄（18~70）',
    city           VARCHAR(20)  NOT NULL COMMENT '所在城市（集中于常见城市，江浙沪皖样本充足）',
    member_level   VARCHAR(10)  NOT NULL COMMENT '会员等级：普通/银卡/金卡/黑金',
    registered_at  DATETIME     NOT NULL COMMENT '注册时间',
    PRIMARY KEY (customer_id),
    KEY idx_city (city),
    KEY idx_member_level (member_level)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='客户表';

CREATE TABLE IF NOT EXISTS products (
    product_id    BIGINT        NOT NULL AUTO_INCREMENT COMMENT '商品ID',
    product_name  VARCHAR(100)  NOT NULL COMMENT '商品名称',
    category      VARCHAR(20)   NOT NULL COMMENT '一级品类：数码/服饰/家居/美妆/食品/运动',
    sub_category  VARCHAR(20)   NOT NULL COMMENT '二级品类',
    brand         VARCHAR(30)   NOT NULL COMMENT '品牌',
    unit_price    DECIMAL(10,2) NOT NULL COMMENT '单价（元）',
    cost          DECIMAL(10,2) NOT NULL COMMENT '成本（元），恒小于 unit_price',
    status        VARCHAR(10)   NOT NULL COMMENT '状态：上架/下架（下架商品仍可有历史订单）',
    PRIMARY KEY (product_id),
    KEY idx_category (category),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='商品表';

CREATE TABLE IF NOT EXISTS orders (
    order_id      BIGINT        NOT NULL AUTO_INCREMENT COMMENT '订单ID',
    customer_id   BIGINT        NOT NULL COMMENT '下单客户ID（customers.customer_id）',
    order_time    DATETIME      NOT NULL COMMENT '下单时间（2024-01-01 ~ 2026-09-30）',
    status        VARCHAR(10)   NOT NULL COMMENT '订单状态：待支付/已支付/已发货/已完成/已取消',
    channel       VARCHAR(10)   NOT NULL COMMENT '下单渠道：App/小程序/H5/PC',
    total_amount  DECIMAL(12,2) NOT NULL COMMENT '订单总额（元）= 明细 pay_amount 合计',
    PRIMARY KEY (order_id),
    KEY idx_customer (customer_id),
    KEY idx_order_time (order_time),
    KEY idx_status (status),
    KEY idx_channel (channel)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单主表';

CREATE TABLE IF NOT EXISTS order_items (
    item_id          BIGINT        NOT NULL AUTO_INCREMENT COMMENT '明细ID',
    order_id         BIGINT        NOT NULL COMMENT '所属订单ID（orders.order_id）',
    product_id       BIGINT        NOT NULL COMMENT '商品ID（products.product_id）',
    quantity         INT           NOT NULL COMMENT '数量（1~5）',
    unit_price       DECIMAL(10,2) NOT NULL COMMENT '成交单价（元），快照自商品',
    discount_amount  DECIMAL(10,2) NOT NULL DEFAULT 0.00 COMMENT '优惠金额（元），≤ 单行小计的 15%',
    pay_amount       DECIMAL(12,2) NOT NULL COMMENT '实付金额（元）= unit_price*quantity - discount_amount',
    PRIMARY KEY (item_id),
    KEY idx_order (order_id),
    KEY idx_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='订单明细表';

CREATE TABLE IF NOT EXISTS payments (
    payment_id  BIGINT        NOT NULL AUTO_INCREMENT COMMENT '支付单ID',
    order_id    BIGINT        NOT NULL COMMENT '订单ID（orders.order_id），已支付单 1:1',
    method      VARCHAR(10)   NOT NULL COMMENT '支付方式：微信/支付宝/银行卡/余额',
    pay_time    DATETIME      NOT NULL COMMENT '支付时间（晚于下单 0~2 小时）',
    amount      DECIMAL(12,2) NOT NULL COMMENT '支付金额（元）= orders.total_amount',
    status      VARCHAR(10)   NOT NULL COMMENT '支付状态：成功/已退款（失败不落单）',
    PRIMARY KEY (payment_id),
    KEY idx_order (order_id),
    KEY idx_method (method),
    KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='支付表（与已支付订单 1:1）';

CREATE TABLE IF NOT EXISTS refunds (
    refund_id    BIGINT        NOT NULL AUTO_INCREMENT COMMENT '退款单ID',
    order_id     BIGINT        NOT NULL COMMENT '订单ID（orders.order_id）',
    refund_time  DATETIME      NOT NULL COMMENT '退款时间（晚于支付 0~15 天）',
    amount       DECIMAL(12,2) NOT NULL COMMENT '退款金额（元），≤ 该单支付金额的 60%~100%',
    reason       VARCHAR(20)   NOT NULL COMMENT '退款原因：质量问题/不想要/物流问题/其他',
    PRIMARY KEY (refund_id),
    KEY idx_order (order_id),
    KEY idx_reason (reason)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='退款表（约占已支付单的 5%）';
