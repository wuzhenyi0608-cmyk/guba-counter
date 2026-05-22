import re
import json
import time
import requests
from datetime import date
from flask import Flask, request, render_template_string


class EastmoneyGubaBinaryApiCounter:
    def __init__(
        self,
        stock_code: str,
        target_date_str: str,
        max_pages: int = 1000,
        page_size: int = 40,
        scan_range: int = 30,
        exclude_keyword: str = "股份",
    ):
        self.stock_code = stock_code.strip()
        self.target_date_str = target_date_str.strip()
        self.max_pages = max_pages
        self.page_size = page_size
        self.scan_range = scan_range
        self.exclude_keyword = exclude_keyword.strip()

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

    def get_post_date_str(self, item):
        """
        只取日期字符串。
        接口返回示例：
        post_publish_time = "2026-05-21 18:23:22"
        返回：
        "2026-05-21"
        """
        post_time = str(item.get("post_publish_time", "")).strip()

        if len(post_time) >= 10:
            return post_time[:10]

        return ""

    def valid_date_str(self, s: str) -> bool:
        return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s))

    def should_count_author(self, author: str) -> bool:
        """
        作者名包含排除关键词，不统计。
        例如：排除关键词 = 股份
        采纳股份资讯 => 不统计
        """
        author = str(author or "").strip()

        if not author:
            return True

        if self.exclude_keyword and self.exclude_keyword in author:
            return False

        return True

    def fetch_page(self, page_num: int):
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

        resp = self.session.get(url, params=params, timeout=15)
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
        获取某页日期范围。
        不按作者过滤，因为二分只需要页面日期范围。
        日期只用字符串比较：YYYY-MM-DD。
        """
        try:
            posts, total_count = self.fetch_page(page_num)
        except Exception as e:
            return {
                "ok": False,
                "page": page_num,
                "posts": [],
                "total_count": None,
                "newest": "",
                "oldest": "",
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
                "posts": posts,
                "total_count": total_count,
                "newest": "",
                "oldest": "",
                "error": "未解析到日期",
            }

        # YYYY-MM-DD 可以直接字符串比较大小
        return {
            "ok": True,
            "page": page_num,
            "posts": posts,
            "total_count": total_count,
            "newest": max(dates),
            "oldest": min(dates),
            "error": "",
        }

    def locate_page_by_binary_search(self):
        """
        二分定位目标日期附近页码。
        页面越往后，日期越旧。
        """
        checked = []

        first = self.get_page_date_range(1)
        checked.append(first)

        if not first["ok"]:
            return {
                "center_page": 1,
                "checked": checked,
                "message": "第 1 页没有解析到日期，无法二分定位。",
                "total_count": None,
            }

        total_count = first.get("total_count")

        # 目标日期在第一页范围内
        if first["oldest"] <= self.target_date_str <= first["newest"]:
            return {
                "center_page": 1,
                "checked": checked,
                "message": "目标日期在第 1 页附近。",
                "total_count": total_count,
            }

        # 目标日期比最新帖子还新
        if self.target_date_str > first["newest"]:
            return {
                "center_page": 1,
                "checked": checked,
                "message": "目标日期比最新帖子日期还新。",
                "total_count": total_count,
            }

        last = self.get_page_date_range(self.max_pages)
        checked.append(last)

        if last.get("total_count") is not None:
            total_count = last.get("total_count")

        if last["ok"]:
            # 最大页数仍然比目标日期新，说明还没翻到目标日期
            if self.target_date_str < last["oldest"]:
                return {
                    "center_page": self.max_pages,
                    "checked": checked,
                    "message": "最大页数仍未翻到目标日期，请调大最大页数。",
                    "total_count": total_count,
                }

            # 目标日期在最大页附近
            if last["oldest"] <= self.target_date_str <= last["newest"]:
                return {
                    "center_page": self.max_pages,
                    "checked": checked,
                    "message": "目标日期在最大页数附近。",
                    "total_count": total_count,
                }

        low = 1
        high = self.max_pages
        best_page = None

        while low <= high:
            mid = (low + high) // 2

            info = self.get_page_date_range(mid)
            checked.append(info)

            if info.get("total_count") is not None:
                total_count = info.get("total_count")

            if not info["ok"]:
                high = mid - 1
                continue

            newest = info["newest"]
            oldest = info["oldest"]

            if oldest <= self.target_date_str <= newest:
                best_page = mid
                break

            # 当前页整体比目标日期新，要往后翻
            if oldest > self.target_date_str:
                low = mid + 1

            # 当前页整体比目标日期旧，要往前翻
            elif newest < self.target_date_str:
                high = mid - 1

        if best_page is None:
            best_page = max(1, min(low, self.max_pages))
            message = f"没有精确命中，使用第 {best_page} 页附近扫描。"
        else:
            message = f"二分定位到第 {best_page} 页附近。"

        return {
            "center_page": best_page,
            "checked": checked,
            "message": message,
            "total_count": total_count,
        }

    def count_by_date(self):
        total = 0
        skipped_by_author = 0
        failed_pages = 0
        all_dates = []

        start_ts = time.time()

        locate = self.locate_page_by_binary_search()

        center_page = locate["center_page"]
        total_count = locate.get("total_count")
        locate_message = locate["message"]
        checked = locate["checked"]

        scan_start = max(1, center_page - self.scan_range)
        scan_end = min(self.max_pages, center_page + self.scan_range)

        checked_summary = []

        for item in checked:
            if item["ok"]:
                checked_summary.append(
                    f"第 {item['page']} 页：{item['newest']} ~ {item['oldest']}"
                )
            else:
                checked_summary.append(
                    f"第 {item['page']} 页：未解析到日期"
                )

        for page_num in range(scan_start, scan_end + 1):
            print(f"正在扫描第 {page_num} 页")

            try:
                posts, count = self.fetch_page(page_num)

                if count is not None:
                    total_count = count

            except Exception as e:
                failed_pages += 1
                print(f"第 {page_num} 页请求失败，已跳过：{e}")
                continue

            for item in posts:
                post_date_str = self.get_post_date_str(item)

                if not self.valid_date_str(post_date_str):
                    continue

                all_dates.append(post_date_str)

                # 只统计目标日期
                if post_date_str != self.target_date_str:
                    continue

                author = item.get("user_nickname", "")

                # 作者名包含“股份”的不统计
                if not self.should_count_author(author):
                    skipped_by_author += 1
                    continue

                total += 1

            time.sleep(0.005)

        if all_dates:
            scanned_newest = max(all_dates)
            scanned_oldest = min(all_dates)
        else:
            scanned_newest = ""
            scanned_oldest = ""

        target_covered = False

        if scanned_newest and scanned_oldest:
            target_covered = scanned_oldest <= self.target_date_str <= scanned_newest

        if total > 0:
            message = "统计完成。"
        else:
            message = "扫描范围内没有找到目标日期帖子。请看扫描日期范围是否覆盖目标日期。"

        if not target_covered:
            message += " 注意：扫描日期范围没有覆盖目标日期，请调大附近扫描页数或最大页数。"

        return {
            "stock_code": self.stock_code,
            "target_date": self.target_date_str,
            "exclude_keyword": self.exclude_keyword,
            "total_posts": total,
            "skipped_by_author": skipped_by_author,
            "center_page": center_page,
            "scan_start": scan_start,
            "scan_end": scan_end,
            "failed_pages": failed_pages,
            "total_count": total_count,
            "scanned_newest": scanned_newest or "未解析到日期",
            "scanned_oldest": scanned_oldest or "未解析到日期",
            "target_covered": target_covered,
            "checked_summary": checked_summary,
            "locate_message": locate_message,
            "cost_seconds": round(time.time() - start_ts, 2),
            "message": message,
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
            width: 150px;
        }
        input[name="stock_code"] {
            width: 160px;
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
    max_pages_str = request.args.get("max_pages", "1000").strip()
    page_size_str = request.args.get("page_size", "40").strip()
    scan_range_str = request.args.get("scan_range", "30").strip()
    exclude_keyword = request.args.get("exclude_keyword", "股份").strip()

    result = None
    error = None

    if not target_date_str:
        target_date_str = str(date.today())

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", target_date_str):
        error = "日期格式错误，请使用 YYYY-MM-DD，例如 2026-05-21。"

    try:
        max_pages = int(max_pages_str)
    except ValueError:
        max_pages = 1000

    try:
        page_size = int(page_size_str)
    except ValueError:
        page_size = 40

    try:
        scan_range = int(scan_range_str)
    except ValueError:
        scan_range = 30

    max_pages = max(1, min(max_pages, 10000))
    page_size = max(10, min(page_size, 40))
    scan_range = max(1, min(scan_range, 500))

    if stock_code and not error:
        counter = EastmoneyGubaBinaryApiCounter(
            stock_code=stock_code,
            target_date_str=target_date_str,
            max_pages=max_pages,
            page_size=page_size,
            scan_range=scan_range,
            exclude_keyword=exclude_keyword,
        )
        result = counter.count_by_date()

    return render_template_string(
        HTML,
        stock_code=stock_code,
        target_date=target_date_str,
        max_pages=max_pages,
        page_size=page_size,
        scan_range=scan_range,
        exclude_keyword=exclude_keyword,
        result=result,
        error=error
    )


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)