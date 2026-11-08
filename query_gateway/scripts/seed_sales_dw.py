# -*- coding: utf-8 -*-
import argparse
import calendar
import json
import random
import urllib.request
import urllib.parse
import urllib.error
from datetime import date, datetime, timedelta
from decimal import Decimal

CH_HTTP = "http://192.168.1.136:8123/"
START = date(2023, 1, 1)
END = date(2025, 10, 12)
BATCH = 50000
SEED = 42

REGIONS = [
    ("华东", "上海", "上海", "一线", 10),
    ("华东", "江苏", "南京", "新一线", 5),
    ("华东", "浙江", "杭州", "新一线", 6),
    ("华东", "江苏", "苏州", "新一线", 5),
    ("华东", "浙江", "宁波", "二线", 3),
    ("华东", "安徽", "合肥", "二线", 3),
    ("华东", "浙江", "温州", "二线", 2),
    ("华北", "北京", "北京", "一线", 10),
    ("华北", "天津", "天津", "新一线", 4),
    ("华北", "河北", "石家庄", "二线", 2),
    ("华北", "山西", "太原", "二线", 2),
    ("华北", "河北", "唐山", "三线及以下", 1),
    ("华南", "广东", "广州", "一线", 9),
    ("华南", "广东", "深圳", "一线", 10),
    ("华南", "广东", "佛山", "新一线", 4),
    ("华南", "广东", "东莞", "新一线", 4),
    ("华南", "广西", "南宁", "二线", 2),
    ("华南", "福建", "厦门", "新一线", 3),
    ("华南", "福建", "福州", "二线", 2),
    ("华中", "湖北", "武汉", "新一线", 5),
    ("华中", "湖南", "长沙", "新一线", 4),
    ("华中", "河南", "郑州", "新一线", 4),
    ("华中", "江西", "南昌", "二线", 2),
    ("华中", "河南", "洛阳", "三线及以下", 1),
    ("西南", "四川", "成都", "新一线", 7),
    ("西南", "重庆", "重庆", "新一线", 6),
    ("西南", "云南", "昆明", "二线", 2),
    ("西南", "贵州", "贵阳", "二线", 2),
    ("西北", "陕西", "西安", "新一线", 4),
    ("西北", "甘肃", "兰州", "二线", 1),
    ("西北", "宁夏", "银川", "三线及以下", 1),
    ("东北", "辽宁", "沈阳", "新一线", 3),
    ("东北", "黑龙江", "哈尔滨", "二线", 2),
    ("东北", "吉林", "长春", "二线", 1),
    ("东北", "辽宁", "大连", "二线", 2),
]

CATALOG = {
    "家用电器": (["冰箱", "洗衣机", "空调", "电视"],
                ["美的", "海尔", "格力", "海信", "TCL"],
                (1500, 12000), (0.62, 0.75), 2),
    "数码电子": (["手机", "笔记本电脑", "平板电脑", "无线耳机"],
                ["华为", "小米", "苹果", "OPPO", "vivo"],
                (800, 9000), (0.55, 0.72), 2),
    "办公用品": (["打印机", "投影仪", "碎纸机", "办公椅"],
                ["得力", "晨光", "惠普", "爱普生"],
                (200, 4000), (0.50, 0.70), 3),
    "家居家具": (["沙发", "床垫", "餐桌", "衣柜"],
                ["全友", "顾家家居", "宜家", "林氏木业"],
                (800, 8000), (0.55, 0.70), 2),
    "食品饮料": (["零食礼盒", "饮用水", "乳制品", "咖啡"],
                ["伊利", "蒙牛", "农夫山泉", "三只松鼠", "雀巢"],
                (20, 500), (0.45, 0.65), 15),
    "个护美妆": (["面膜", "洗发水", "香水", "护肤套装"],
                ["珀莱雅", "百雀羚", "欧莱雅", "兰蔻"],
                (50, 1200), (0.35, 0.60), 10),
}
MODELS = ["标准版", "旗舰版", "Pro", "Max", "轻奢款", "经典款", "尊享款", "青春版"]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘蒋蔡余杜叶程苏魏吕丁任沈姚卢")
GIVEN_CHARS = list("伟芳娜秀英敏静丽强磊军洋勇艳杰娟涛明超秀兰霞平刚桂英华玉兰凤云萍素珍美玲丽华丹红梅琳勇军杰涛鑫浩宇轩梓涵子轩欣怡思雨嘉怡浩然梦琪雨泽俊杰雅婷")
LEVELS = [("普通", 60), ("银卡", 25), ("金卡", 12), ("钻石", 3)]
LEVEL_UPGRADE = {"普通": "银卡", "银卡": "金卡", "金卡": "钻石"}
SALES_REPS = ["王建国", "李秀芳", "张伟", "陈静", "刘强", "赵丽", "孙鹏", "周敏", "吴磊", "郑洁"]

MONTH_FACTOR = {1: 1.10, 2: 0.68, 3: 1.00, 4: 0.96, 5: 1.06, 6: 1.18,
                7: 0.94, 8: 0.95, 9: 1.00, 10: 1.06, 11: 1.42, 12: 1.30}
YEAR_FACTOR = {2023: 1.00, 2024: 1.26, 2025: 1.48}


def q2(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01")))


def ch_post(sql: str, body: bytes = b""):
    url = CH_HTTP + "?" + urllib.parse.urlencode({"query": sql})
    req = urllib.request.Request(url, data=body if body is not None else b"",
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse 错误: {detail[:500]}") from None


class Inserter:
    def __init__(self, table: str):
        self.table = table
        self.buf: list[str] = []
        self.total = 0

    def add(self, row: dict):
        self.buf.append(json.dumps(row, ensure_ascii=False))
        if len(self.buf) >= BATCH:
            self.flush()

    def flush(self):
        if not self.buf:
            return
        body = ("\n".join(self.buf) + "\n").encode("utf-8")
        ch_post(f"INSERT INTO {self.table} FORMAT JSONEachRow", body)
        self.total += len(self.buf)
        print(f"    {self.table}: 已写入 {self.total} 行")
        self.buf.clear()


def build_regions() -> list[dict]:
    now = int(datetime(2023, 1, 1).timestamp())
    return [{
        "region_id": i + 1,
        "region_name": r[0], "province": r[1], "city": r[2], "city_tier": r[3],
        "_version": now,
    } for i, r in enumerate(REGIONS)]


def build_products(rng: random.Random) -> list[dict]:
    rows = []
    pid = 0
    now = int(datetime(2023, 1, 1).timestamp())
    for category, (subs, brands, (pmin, pmax), (cmin, cmax), _) in CATALOG.items():
        for brand in brands:
            for sub in subs:
                n_models = 2 if pid < 184 else 1  
                for _ in range(n_models):
                    pid += 1
                    list_price = q2(rng.uniform(pmin, pmax))
                    cost_ratio = rng.uniform(cmin, cmax)
                    rows.append({
                        "product_id": pid,
                        "product_name": f"{brand} {sub} {rng.choice(MODELS)}",
                        "category": category,
                        "sub_category": sub,
                        "brand": brand,
                        "standard_cost": q2(list_price * cost_ratio),
                        "list_price": list_price,
                        "_version": now,
                    })
                    if pid >= 200:
                        return rows
    return rows


def build_customers(rng: random.Random, regions) -> list[dict]:
    rows = []
    weights = [r[4] for r in REGIONS]
    level_names = [l[0] for l in LEVELS]
    level_w = [l[1] for l in LEVELS]
    reg_start = date(2020, 1, 1)
    days_span = (date(2025, 9, 30) - reg_start).days
    for cid in range(1, 2001):
        name = rng.choice(SURNAMES) + rng.choice(GIVEN_CHARS) + (
            rng.choice(GIVEN_CHARS) if rng.random() < 0.45 else "")
        reg_date = reg_start + timedelta(days=rng.randint(0, days_span))
        region_id = rng.choices(range(1, len(regions) + 1), weights=weights)[0]
        level = rng.choices(level_names, weights=level_w)[0]
        gender = rng.choices(["男", "女"], weights=[48, 52])[0]
        v1 = int(datetime.combine(reg_date, datetime.min.time()).timestamp())
        rows.append({
            "customer_id": cid, "customer_name": name, "gender": gender,
            "age": rng.randint(18, 65), "customer_level": level,
            "register_date": reg_date.isoformat(), "region_id": region_id,
            "_version": v1,
        })
        if reg_date < date(2024, 6, 1) and level in LEVEL_UPGRADE and rng.random() < 0.03:
            v2_row = dict(rows[-1])
            v2_row["customer_level"] = LEVEL_UPGRADE[level]
            upgrade_day = rng.randint(180, 700)
            v2_dt = datetime.combine(reg_date, datetime.min.time()) + timedelta(days=upgrade_day)
            if v2_dt.date() <= END:
                v2_row["_version"] = int(v2_dt.timestamp())
                rows.append(v2_row)
    return rows


def generate_facts(rng: random.Random, products, customers):
    cust_by_id = {}
    for c in customers:  
        cust_by_id[c["customer_id"]] = c
    cust_ids = list(cust_by_id.keys())

    prod_by_id = {p["product_id"]: p for p in products}
    cat_of = {pid: prod_by_id[pid]["category"] for pid in prod_by_id}
    qty_cap = {c: v[4] for c, v in CATALOG.items()}
    region_ids = list(range(1, len(REGIONS) + 1))
    region_w = [r[4] for r in REGIONS]
    level_discount = {"钻石": (0.80, 0.95), "金卡": (0.85, 0.97),
                      "银卡": (0.90, 1.00), "普通": (0.95, 1.00)}

    day = START
    while day <= END:
        lam = 120 * YEAR_FACTOR[day.year] * MONTH_FACTOR[day.month]
        wd = day.weekday()
        wd_factor = 1.08 if wd >= 5 else 0.98
        n_orders = max(1, int(rng.gauss(lam * wd_factor, lam * 0.12)))

        for daily_seq in range(1, n_orders + 1):
            order_id = int(day.strftime("%Y%m%d")) * 10000 + daily_seq
            customer = cust_by_id[rng.choices(cust_ids, weights=_cust_weights(rng, len(cust_ids)))[0]]
            cid = customer["customer_id"]
            level = customer["customer_level"]
            channel = rng.choices(["线上商城", "线下门店", "分销"], weights=[55, 35, 10])[0]
            region_id = (customer["region_id"] if rng.random() < 0.7
                         else rng.choices(region_ids, weights=region_w)[0])
            if channel == "线上商城":
                pay = rng.choices(["微信支付", "支付宝", "银行卡"], weights=[45, 42, 13])[0]
                rep = ""
            elif channel == "线下门店":
                pay = rng.choices(["微信支付", "现金", "银行卡", "支付宝"], weights=[40, 22, 25, 13])[0]
                rep = rng.choice(SALES_REPS)
            else:
                pay = "对公转账"
                rep = ""

            n_lines = rng.choices([1, 2, 3, 4], weights=[25, 40, 25, 10])[0]
            chosen = rng.sample(list(prod_by_id.values()),
                                k=min(n_lines, len(prod_by_id)))
            hour = rng.choices(range(8, 23),
                               weights=[3, 4, 6, 7, 8, 9, 8, 7, 8, 10, 12, 11, 8, 5, 3])[0]
            minute = rng.randint(0, 59)
            second = rng.randint(0, 59)
            dt = datetime(day.year, day.month, day.day, hour, minute, second)
            v1 = int(calendar.timegm(dt.timetuple()))
            is_promo = day.month in (6, 11)

            lines_v1 = []
            for line_no, prod in enumerate(chosen, start=1):
                cap = qty_cap[cat_of[prod["product_id"]]]
                qty = rng.randint(1, cap)
                unit_price = q2(prod["list_price"] * rng.uniform(0.97, 1.03))
                if is_promo:
                    disc = q2(rng.uniform(0.65, 0.90))
                else:
                    lo, hi = level_discount[level]
                    disc = q2(rng.uniform(lo, hi))
                sales = q2(qty * unit_price * disc)
                cost = q2(qty * prod["standard_cost"])
                lines_v1.append({
                    "order_id": order_id, "line_no": line_no,
                    "order_date": day.isoformat(),
                    "order_time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "customer_id": cid, "product_id": prod["product_id"],
                    "region_id": region_id, "channel": channel,
                    "sales_rep": rep, "pay_method": pay,
                    "quantity": qty, "unit_price": unit_price,
                    "discount_rate": disc, "sales_amount": sales,
                    "cost_amount": cost, "profit_amount": q2(sales - cost),
                    "order_status": "completed", "_version": v1,
                })

            yield from lines_v1

            if day <= END - timedelta(days=20) and rng.random() < 0.10:
                roll = rng.random()
                if roll < 0.40:        
                    new_status, delta_days = "returned", rng.randint(3, 15)
                    change = "status"
                elif roll < 0.80:      
                    new_status, delta_days = "cancelled", rng.randint(0, 2)
                    change = "status"
                else:                  
                    new_status, delta_days = "completed", rng.randint(0, 3)
                    change = rng.choice(["price", "qty"])
                v2 = v1 + delta_days * 86400
                for row in lines_v1:
                    nr = dict(row)
                    nr["_version"] = v2
                    nr["order_status"] = new_status
                    if change == "price" and row is lines_v1[0]:
                        nr["discount_rate"] = q2(max(0.5, row["discount_rate"] - 0.1))
                        nr["sales_amount"] = q2(row["quantity"] * row["unit_price"] * nr["discount_rate"])
                        nr["profit_amount"] = q2(nr["sales_amount"] - row["cost_amount"])
                    elif change == "qty" and row is lines_v1[0]:
                        nr["quantity"] = max(1, row["quantity"] + rng.choice([-1, 1]))
                        nr["sales_amount"] = q2(nr["quantity"] * row["unit_price"] * row["discount_rate"])
                        prod = prod_by_id[row["product_id"]]
                        nr["cost_amount"] = q2(nr["quantity"] * prod["standard_cost"])
                        nr["profit_amount"] = q2(nr["sales_amount"] - nr["cost_amount"])
                    yield nr
        day += timedelta(days=1)


_W_CACHE = None


def _cust_weights(rng, n):
    global _W_CACHE
    if _W_CACHE is None or len(_W_CACHE) != n:
        _W_CACHE = [1.0 / (i + 1) ** 0.55 for i in range(n)]
        rng.shuffle(_W_CACHE)
    return _W_CACHE


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-truncate", action="store_true")
    args = ap.parse_args()

    rng = random.Random(SEED)

    if not args.no_truncate:
        for t in ("dim_region", "dim_product", "dim_customer", "fact_sales_order_line"):
            ch_post(f"TRUNCATE TABLE sales_dw.{t}")
        print("已 TRUNCATE sales_dw 四张表")

    print("[1/4] 灌入 dim_region")
    ins = Inserter("sales_dw.dim_region")
    for r in build_regions():
        ins.add(r)
    ins.flush()

    print("[2/4] 灌入 dim_product")
    products = build_products(rng)
    ins = Inserter("sales_dw.dim_product")
    for p in products:
        ins.add(p)
    ins.flush()

    print("[3/4] 灌入 dim_customer（含升级双版本）")
    customers = build_customers(rng, build_regions())
    ins = Inserter("sales_dw.dim_customer")
    for c in customers:
        ins.add(c)
    ins.flush()

    print("[4/4] 灌入 fact_sales_order_line（含 CDC 双版本，约需 1-2 分钟）")
    ins = Inserter("sales_dw.fact_sales_order_line")
    for row in generate_facts(rng, products, customers):
        ins.add(row)
    ins.flush()
    print("造数完成。")


if __name__ == "__main__":
    main()
