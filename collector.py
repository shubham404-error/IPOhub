import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    IPOJI_BASE_URL,
    IPOJI_CURRENT_IPO_URL,
    REQUEST_TIMEOUT,
    DETAIL_LIMIT,
)

from database import Database


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/139.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

MONTHS = (
    "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|"
    "Sep|Oct|Nov|Dec"
)


class IPOJiClient:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get(self, url):

        response = self.session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response

    def get_current_ipos(self):

        url = urljoin(
            IPOJI_BASE_URL,
            IPOJI_CURRENT_IPO_URL
        )

        response = self.get(url)

        soup = BeautifulSoup(
            response.text,
            "lxml"
        )

        return self._parse_current(
            soup,
            response.url
        )

    def _parse_current(
        self,
        soup,
        page_url
    ):

        now = datetime.now(
            timezone.utc
        ).isoformat()

        rows = []
        seen = set()

        for heading in soup.find_all(
            ["h2", "h3"]
        ):

            company = heading.get_text(
                " ",
                strip=True
            )

            if not company:
                continue

            card = self._find_card(
                heading
            )

            if card is None:
                continue

            text = card.get_text(
                " ",
                strip=True
            )

            if "Offer Price" not in text:
                continue

            detail_url = self._find_detail_url(
                card,
                page_url
            )

            if not detail_url:
                continue

            source_id = (
                detail_url
                .rstrip("/")
                .split("/")[-1]
            )

            if source_id in seen:
                continue

            row = self._parse_card(
                text,
                detail_url,
                company,
                now
            )

            if row:

                seen.add(source_id)

                rows.append(row)

        return rows

    @staticmethod
    def _find_card(heading):

        current = heading

        for _ in range(8):

            current = current.parent

            if current is None:
                return None

            text = current.get_text(
                " ",
                strip=True
            )

            headings = current.find_all(
                ["h2", "h3"]
            )

            if (
                "Offer Price" in text
                and "Lot Size" in text
                and len(headings) == 1
            ):
                return current

        return None

    @staticmethod
    def _find_detail_url(
        card,
        page_url
    ):

        for link in card.find_all(
            "a",
            href=True
        ):

            url = urljoin(
                page_url,
                link["href"]
            )

            if (
                "/ipo/" in url
                and "check" not in url.lower()
            ):
                return url

        return None

    def _parse_card(
        self,
        text,
        url,
        company,
        collected_at
    ):

        # FIXED DATE REGEX
        dates = re.search(
            rf"({MONTHS})\s+"
            rf"(\d{{1,2}}),\s+"
            rf"(\d{{4}})"
            rf"\s*[–-]\s*"
            rf"({MONTHS})\s+"
            rf"(\d{{1,2}}),\s+"
            rf"(\d{{4}})",
            text,
            re.I
        )

        price = re.search(
            r"Offer Price\s+₹\s*"
            r"([\d,]+(?:\.\d+)?)"
            r"(?:\s*-\s*"
            r"([\d,]+(?:\.\d+)?))?",
            text,
            re.I
        )

        lot = re.search(
            r"Lot Size\s+([\d,]+)",
            text,
            re.I
        )

        issue = re.search(
            r"Issue Size\s+₹\s*"
            r"([\d,.]+)\s*Cr",
            text,
            re.I
        )

        subscription = re.search(
            r"Subscription\s+"
            r"([\d,.]+)x",
            text,
            re.I
        )

        segment = re.search(
            r"(Mainboard|BSE SME|NSE SME|SME)",
            text,
            re.I
        )

        status = re.search(
            r"(Live|Pre-Apply|Open|Closed|"
            r"Allotment Out|Allotment Awaited|"
            r"Tentative Dates|DRHP Approved)",
            text,
            re.I
        )

        if not any(
            [
                price,
                lot,
                issue,
                dates
            ]
        ):
            return None

        slug = (
            url
            .rstrip("/")
            .split("/")[-1]
        )

        subscription_url = urljoin(
            IPOJI_BASE_URL,
            f"/ipo-subscription/{slug}"
        )

        gmp_url = urljoin(
            IPOJI_BASE_URL,
            f"/ipo-gmp/{slug}"
        )

        return {

            "source": "ipoji",

            "source_id": slug,

            "company_name": company,

            "symbol": None,

            "ipo_type": "IPO",

            "segment": (
                segment.group(1)
                if segment
                else None
            ),

            "status": (
                status.group(1)
                if status
                else None
            ),

            "open_date": (
                f"{dates.group(1)} "
                f"{dates.group(2)}, "
                f"{dates.group(3)}"
                if dates
                else None
            ),

            "close_date": (
                f"{dates.group(4)} "
                f"{dates.group(5)}, "
                f"{dates.group(6)}"
                if dates
                else None
            ),

            "listing_date": None,

            "price_low": (
                num(price.group(1))
                if price
                else None
            ),

            "price_high": (
                num(
                    price.group(2)
                    or price.group(1)
                )
                if price
                else None
            ),

            "lot_size": (
                num(lot.group(1))
                if lot
                else None
            ),

            "issue_size": (
                num(issue.group(1))
                if issue
                else None
            ),

            "gmp": None,

            "subscription": (
                num(
                    subscription.group(1)
                )
                if subscription
                else None
            ),

            "source_url": url,

            "detail_url": url,

            "subscription_url": (
                subscription_url
            ),

            "gmp_url": gmp_url,

            "collected_at": collected_at,

            "raw": {
                "current_text": text
            },
        }

    def enrich(self, row):

        detail = self.parse_detail(
            row["detail_url"]
        )

        subscription = self.parse_subscription(
            row["subscription_url"]
        )

        gmp = self.parse_gmp(
            row["gmp_url"],
            row.get("price_high")
        )

        row.update(detail)
        row.update(subscription)
        row.update(gmp)

        return row

    def parse_detail(self, url):

        try:

            response = self.get(url)

            soup = BeautifulSoup(
                response.text,
                "lxml"
            )

            text = soup.get_text(
                " ",
                strip=True
            )

            listing_date = first_date_after(
                text,
                ["listing date", "listing"]
            )

            allotment_date = first_date_after(
                text,
                ["allotment"]
            )

            listing_exchange = extract_value(
                text,
                ["Listing At", "Listing"]
            )

            registrar = extract_value(
                text,
                ["registrar"]
            )

            lead_managers = extract_value(
                text,
                [
                    "lead manager",
                    "book-running lead manager"
                ]
            )

            fresh_issue = extract_crore(
                text,
                ["fresh issue"]
            )

            ofs_issue = extract_crore(
                text,
                ["OFS", "offer for sale"]
            )

            return {

                "listing_date": listing_date,

                "allotment_date": allotment_date,

                "listing_exchange": listing_exchange,

                "registrar": registrar,

                "lead_managers": lead_managers,

                "fresh_issue": fresh_issue,

                "ofs_issue": ofs_issue,

                "raw": {
                    "detail_text": text[:12000]
                },
            }

        except Exception as exc:

            return {
                "raw": {
                    "detail_error": str(exc)
                }
            }

    def parse_subscription(self, url):

        try:

            response = self.get(url)

            soup = BeautifulSoup(
                response.text,
                "lxml"
            )

            text = soup.get_text(
                " ",
                strip=True
            )

            values = {}

            for table in soup.find_all(
                "table"
            ):

                for tr in table.find_all(
                    "tr"
                ):

                    cells = [
                        clean(
                            x.get_text(
                                " ",
                                strip=True
                            )
                        )
                        for x in tr.find_all(
                            ["td", "th"]
                        )
                    ]

                    if len(cells) < 2:
                        continue

                    label = cells[0].lower()

                    nums = [
                        parse_x(x)
                        for x in cells[1:]
                    ]

                    nums = [
                        x
                        for x in nums
                        if x is not None
                    ]

                    if not nums:
                        continue

                    value = nums[-1]

                    if "qib" in label:
                        values["qib"] = value

                    elif (
                        "bnii" in label
                        or "b-ni" in label
                    ):
                        values["bnii"] = value

                    elif (
                        "snii" in label
                        or "s-ni" in label
                    ):
                        values["snii"] = value

                    elif "nii" in label:
                        values["nii"] = value

                    elif (
                        "retail" in label
                        or "individual" in label
                    ):
                        values["retail"] = value

                    elif "employee" in label:
                        values["employee"] = value

                    elif "other" in label:
                        values["others"] = value

                    elif "total" in label:
                        values["total"] = value

            # Fallback for card-style layouts

            for label, key in [
                ("QIB (x)", "qib"),
                ("NII (x)", "nii"),
                ("Retail (x)", "retail"),
                ("Total (x)", "total"),
            ]:

                if key not in values:

                    match = re.search(
                        re.escape(label)
                        + r"\s+([\d,.]+)",
                        text,
                        re.I
                    )

                    if match:

                        values[key] = num(
                            match.group(1)
                        )

            apps = re.search(
                r"Applications\s+([\d,]+)",
                text,
                re.I
            )

            values["applications"] = (
                int(
                    apps.group(1)
                    .replace(",", "")
                )
                if apps
                else None
            )

            values[
                "subscription_updated_at"
            ] = datetime.now(
                timezone.utc
            ).isoformat()

            values["raw"] = {
                "subscription_text":
                    text[:12000]
            }

            return values

        except Exception as exc:

            return {
                "raw": {
                    "subscription_error":
                        str(exc)
                }
            }

    def parse_gmp(
        self,
        url,
        price_high
    ):

        try:

            response = self.get(url)

            soup = BeautifulSoup(
                response.text,
                "lxml"
            )

            text = soup.get_text(
                " ",
                strip=True
            )

            gmp_match = re.search(
                r"latest available GMP is "
                r"₹\s*([+-]?[\d,.]+)",
                text,
                re.I
            )

            if not gmp_match:

                gmp_match = re.search(
                    r"GMP\s+"
                    r"([+-]?[\d,.]+)",
                    text,
                    re.I
                )

            gmp = (
                num(gmp_match.group(1))
                if gmp_match
                else None
            )

            pct_match = re.search(
                r"GMP %\s*"
                r"([+-]?[\d,.]+)%",
                text,
                re.I
            )

            gmp_pct = (
                num(pct_match.group(1))
                if pct_match
                else None
            )

            listing_match = re.search(
                r"indicative listing\s+"
                r"₹\s*([\d,.]+)",
                text,
                re.I
            )

            indicative = (
                num(listing_match.group(1))
                if listing_match
                else None
            )

            if (
                indicative is None
                and gmp is not None
                and price_high is not None
            ):

                indicative = (
                    price_high + gmp
                )

            updated_match = re.search(
                r"last recorded on\s+"
                r"([^\.]+)",
                text,
                re.I
            )

            updated = (
                updated_match.group(1).strip()
                if updated_match
                else None
            )

            return {

                "gmp": gmp,

                "gmp_pct": gmp_pct,

                "indicative_listing":
                    indicative,

                "gmp_updated_at":
                    updated,

                "raw": {
                    "gmp_text":
                        text[:12000]
                },
            }

        except Exception as exc:

            return {
                "raw": {
                    "gmp_error": str(exc)
                }
            }


def extract_value(
    text,
    labels
):

    for label in labels:

        match = re.search(
            re.escape(label)
            + r"\s+(.{1,180}?)"
            + r"(?=\s+(?:The|Retail|"
            + r"S-HNI|B-HNI|Face Value|"
            + r"Lot Size|Listing At|"
            + r"Registrar|The total issue)\b|$)",
            text,
            re.I
        )

        if match:

            return match.group(
                1
            ).strip(" .")

    return None


def first_date_after(
    text,
    labels
):

    date_pattern = (
        rf"({MONTHS}\s+\d{{1,2}},\s+\d{{4}}"
        rf"|\d{{1,2}}\s+(?:{MONTHS})\s+\d{{4}})"
    )

    for label in labels:

        match = re.search(
            re.escape(label)
            + r".{0,120}?"
            + date_pattern,
            text,
            re.I
        )

        if match:

            return match.group(1)

    return None


def extract_crore(
    text,
    labels
):

    for label in labels:

        match = re.search(
            re.escape(label)
            + r"[^.]{0,100}?"
            + r"₹?\s*([\d,.]+)"
            + r"\s*crore",
            text,
            re.I
        )

        if match:

            return num(
                match.group(1)
            )

    return None


def parse_x(value):

    match = re.search(
        r"([\d,.]+)\s*x?",
        value,
        re.I
    )

    return (
        num(match.group(1))
        if match
        else None
    )


def clean(value):

    return re.sub(
        r"\s+",
        " ",
        value
    ).strip()


def num(value):

    if value is None:
        return None

    cleaned = re.sub(
        r"[^0-9.\-]",
        "",
        str(value)
    )

    try:

        return (
            float(cleaned)
            if "." in cleaned
            else int(cleaned)
        )

    except (
        TypeError,
        ValueError
    ):

        return None


def collect_once(
    enrich=True
):

    client = IPOJiClient()

    rows = client.get_current_ipos()

    if enrich:

        enriched = []

        for row in rows[:DETAIL_LIMIT]:

            enriched.append(
                client.enrich(row)
            )

        rows = enriched

    database = Database()

    try:

        count = database.upsert_ipos(
            rows
        )

        captured = datetime.now(
            timezone.utc
        ).isoformat()

        for row in rows:

            if (
                row.get("subscription")
                is not None
                or row.get("qib")
                is not None
            ):

                database.add_subscription_snapshot(
                    "ipoji",
                    row["source_id"],
                    {
                        "qib": row.get("qib"),
                        "nii": row.get("nii"),
                        "snii": row.get("snii"),
                        "bnii": row.get("bnii"),
                        "retail": row.get("retail"),
                        "employee": row.get("employee"),
                        "others": row.get("others"),
                        "total": row.get("subscription"),
                        "applications":
                            row.get("applications"),
                        "raw":
                            row.get("raw", {}),
                    },
                    captured
                )

            if row.get("gmp") is not None:

                database.add_gmp_snapshot(
                    "ipoji",
                    row["source_id"],
                    {
                        "gmp": row.get("gmp"),
                        "gmp_pct":
                            row.get("gmp_pct"),
                        "indicative_listing":
                            row.get(
                                "indicative_listing"
                            ),
                        "raw":
                            row.get("raw", {}),
                    },
                    captured
                )

    finally:

        database.close()

    return {
        "count": count,
        "rows": rows
    }


if __name__ == "__main__":

    result = collect_once(
        enrich=True
    )

    print(
        f"Collected and enriched "
        f"{result['count']} IPOs."
    )

    for row in result["rows"]:

        print(
            f"- {row['company_name']} | "
            f"sub={row.get('subscription')}x | "
            f"QIB={row.get('qib')} | "
            f"NII={row.get('nii')} | "
            f"Retail={row.get('retail')} | "
            f"GMP={row.get('gmp')}"
        )
