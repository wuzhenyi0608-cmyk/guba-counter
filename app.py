import re
import json
import time
import requests
from datetime import date
from flask import Flask, request, render_template_string


class EastmoneyGubaFastPageCounter:
    def __init__(
        self,
        stock_code: str,
        target_date_str: str,
        max_pages: int = 5000,
        page_size: int = 65,
    ):
        self.stock_code = stock_code.strip()
        self.target_date_str = target_date_str.strip()
        self.max_pages = max_pages
        self.page_size = page_size

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://guba.eastmoney.com/list,{self.stock_code}.html",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })

    def valid_date_str(self, s: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))

    def get_post_date_str(self, item):
        """
        接口返回：
        post_publish_time = "2026-05-21 18:23:22"

        这里只取前 10 位：
        "2026-05-21"
        """
        post_time = str(item.get("post_publish_time", "")).strip()

        if len(post_time) >= 10:
            return post_time[:10]

        return ""

    def fetch_page(self, page_num: int):
        """
        东方财富股吧列表接口。
        """
        url = "https://gbcdn.dfcfw.com/gbapi/webarticlelist_api_Article_Articlelist.js"
        callback_name = "article_list"

        params = {
            "code": self.stock_code,
            "type": "0",
            "p": str(page_num),
            "ps": str(self.page_size),
            "sorttype": "0",
            "callback": callback_name,
        }

        resp = self.session.get(url, params=params, timeout=8)
        resp.raise_for_status()

        text = resp.text

        # 返回格式：
        # var webarticlelist_api_Article_Articlelist=article_list({...});
        m = re.search(rf"{callback_name}\((.*)\);?", text, re.S)

        if not m:
            return [], None

        data = json.loads(m.group(1))

        posts = data.get("re", [])
        total_count = data.get("count")

        return posts, total_count

    def get_page_date_range(self, page_num: int):
        """
        获取某一页的日期范围。
        日期只用 YYYY-MM-DD 字符串比较。
        """
        try:
            posts, total_count = self.fetch_page(page_num)
        except Exception as e:
            return {
                "ok": False,
                "page": page_num,
                "newest": "",
                "oldest": "",
                "total_count": None,
                "error": str(e),
            }

        dates = []

        for item in posts:
            d = self.get_post_date_str(item)

            if self.valid_date_str(d):
                dates.append(d)

        if not dates:
            return {
                "ok": False,
                "page": page_num,
                "newest": "",
                "oldest": "",
                "total_count": total_count,
                "error": "未解析到日期",
            }

        # YYYY-MM-DD 字符串可以直接比较大小
        return {
            "ok": True,
            "page": page_num,
            "newest": max(dates),
            "oldest": min(dates),
            "total_count": total_count,
            "error": "",
        }

    def page_relation(self, page_num: int):
        """
        判断某页和目标日期的关系。

        contains: 当前页包含目标日期
        newer: 当前页整体比目标日期新，需要往后翻
        older: 当前页整体比目标日期旧，需要往前翻
        invalid: 当前页无效
        """
        info = self.get_page_date_range(page_num)

        if not info["ok"]:
            return "invalid", info

        newest = info["newest"]
        oldest = info["oldest"]

        if oldest <= self.target_date_str <= newest:
            return "contains", info

        if oldest > self.target_date_str:
            return "newer", info

        if newest < self.target_date_str:
            return "older", info

        return "invalid", info

    def find_first_page(self):
        """
        二分查找目标日期第一次出现的页码。
        """
        low = 1
        high = self.max_pages
        ans = None
        last_info = None
        total_count = None

        while low <= high:
            mid = (low + high) // 2
            relation, info = self.page_relation(mid)
            last_info = info

            if info.get("total_count") is not None:
                total_count = info.get("total_count")

            if relation == "contains":
                ans = mid
                high = mid - 1

            elif relation == "newer":
                low = mid + 1

            elif relation == "older":
                high = mid - 1

            else:
                high = mid - 1

        return ans, last_info, total_count

    def find_last_page(self):
        """
        二分查找目标日期最后一次出现的页码。
        """
        low = 1
        high = self.max_pages
        ans = None
        last_info = None
        total_count = None

        while low <= high:
            mid = (low + high) // 2
            relation, info = self.page_relation(mid)
            last_info = info

            if info.get("total_count") is not None:
                total_count = info.get("total_count")

            if relation == "contains":
                ans = mid
                low = mid + 1

            elif relation == "newer":
                low = mid + 1

            elif relation == "older":
                high = mid - 1

            else:
                high = mid - 1

        return ans, last_info, total_count

    def count_by_date(self):
        """
        极速估算版：

        1. 二分找到目标日期出现的第一页
        2. 二分找到目标日期出现的最后一页
        3. 直接计算：

           当天帖子数量 = 日期页数 × 65

        不逐条统计，不过滤作者，不校正第一页/最后一页。
        """
        start_ts = time.time()

        first_page, first_info, total_count1 = self.find_first_page()
        last_page, last_info, total_count2 = self.find_last_page()

        total_count = total_count1 or total_count2

        if first_page is None or last_page is None:
            return {
                "stock_code": self.stock_code,
                "target_date": self.target_date_str,
                "total_posts": 0,
                "first_page": 0,
                "last_page": 0,
                "total_pages": 0,
                "page_size": self.page_size,
                "total_count": total_count,
                "cost_seconds": round(time.time() - start_ts, 2),
                "message": "没有找到目标日期对应的页码，可能是最大页数不够，或该日期没有帖子。",
            }

        total_pages = last_page - first_page + 1
        total_posts = total_pages * self.page_size

        return {
            "stock_code": self.stock_code,
            "target_date": self.target_date_str,
            "total_posts": total_posts,
            "first_page": first_page,
            "last_page": last_page,
            "total_pages": total_pages,
            "page_size": self.page_size,
            "total_count": total_count,
            "cost_seconds": round(time.time() - start_ts, 2),
            "message": f"极速估算完成：第 {first_page} 页到第 {last_page} 页，共 {total_pages} 页 × {self.page_size} 条 = {total_posts} 条。",
        }


app = Flask(__name__)


HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <title>东方财富股吧帖子数量统计</title>
    <style>
        body {
            font-family: Arial, "Microsoft YaHei", sans-serif;
            background: #f5f6f7;
            margin: 0;
            padding: 40px;
        }
        .box {
            max-width: 760px;
            margin: auto;
            background: white;
            border-radius: 12px;
            padding: 28px;
            box-shadow: 0 4px 18px rgba(0,0,0,0.08);
        }
        h1 {
            margin-top: 0;
            font-size: 30px;
        }
        form {
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
            margin-bottom: 24px;
        }
        input {
            padding: 12px;
            font-size: 16px;
            border: 1px solid #ddd;
            border-radius: 8px;
            width: 170px;
        }
        input[name="stock_code"] {
            width: 170px;
        }
        button {
            padding: 12px 24px;
            font-size: 16px;
            background: #ff6a00;
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
        }
        .result {
            background: #fff7ef;
            border: 1px solid #ffd8b5;
            border-radius: 10px;
            padding: 22px;
            margin-top: 20px;
        }
        .count {
            font-size: 60px;
            font-weight: bold;
            color: #e60000;
            margin-top: 10px;
        }
        .info {
            color: #666;
            font-size: 14px;
            line-height: 1.8;
            margin-top: 12px;
        }
        .error {
            background: #fff1f0;
            color: #a8071a;
            border: 1px solid #ffa39e;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 16px;
        }
        label {
            color: #555;
            font-size: 14px;
        }
    </style>
</head>
<body>
<div class="box">
    <h1>东方财富股吧指定日期帖子数量统计</h1>

    <form method="get">
        <label>
            股票代码：
            <input name="stock_code" placeholder="例如 301122、000725"
                   value="{{ stock_code or '' }}">
        </label>

        <label>
            日期：
            <input name="target_date" placeholder="2026-05-21"
                   value="{{ target_date }}">
        </label>

        <button type="submit">查询</button>
    </form>

    {% if error %}
    <div class="error">{{ error }}</div>
    {% endif %}

    {% if result %}
        <div class="result">
            <div>股票代码：<strong>{{ result.stock_code }}</strong></div>
            <div>查询日期：<strong>{{ result.target_date }}</strong></div>
            <div>当天帖子数量：</div>
            <div class="count">{{ result.total_posts }}</div>

            <div class="info">
                页码范围：第 {{ result.first_page }} 页 到 第 {{ result.last_page }} 页；
                共 {{ result.total_pages }} 页；
                耗时 {{ result.cost_seconds }} 秒。
            </div>
        </div>
    {% endif %}
</div>
</body>
</html>
"""


@app.route("/")
def index():
    stock_code = request.args.get("stock_code", "").strip()
    target_date_str = request.args.get("target_date", "").strip()

    # 后台默认参数
    max_pages = 10000
    page_size = 65

    result = None
    error = None

    if not target_date_str:
        target_date_str = str(date.today())

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date_str):
        error = "日期格式错误，请使用 YYYY-MM-DD，例如 2026-05-21。"

    if stock_code and not error:
        try:
            counter = EastmoneyGubaFastPageCounter(
                stock_code=stock_code,
                target_date_str=target_date_str,
                max_pages=max_pages,
                page_size=page_size,
            )

            result = counter.count_by_date()

        except Exception as e:
            error = f"查询失败：{e}"

    return render_template_string(
        HTML,
        stock_code=stock_code,
        target_date=target_date_str,
        result=result,
        error=error
    )


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
