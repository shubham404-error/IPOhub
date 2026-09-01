import re
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from config import (
    IPOJI_BASE_URL,
    IPOJI_CURRENT_IPO_URL,
    REQUEST_TIMEOUT,
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


class IPOJiClient:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get_current_ipos(self):

        url = urljoin(
            IPOJI_BASE_URL,
            IPOJI_CURRENT_IPO_URL
        )

        response = self.session.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        soup = BeautifulSoup(
            response.text,
            "lxml"
        )

        return self._parse(
            soup,
            response.url
        )

    def _parse(self, soup, page_url):

        now = datetime.now(
            timezone.utc
        ).isoformat()

        rows = []
        seen = set()

        # IPO Ji displays the company name
        # in an H2/H3 heading.
        #
        # The previous scraper looked inside
        # the "View" button. That button only
        # contains the word "View", so nothing
        # was being extracted.

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

            if detail_url in seen:
                continue

            row = self._parse_card(
                text=text,
                url=detail_url,
                company=company,
                collected_at=now
            )

            if row:

                seen.add(
                    detail_url
                )

                rows.append(row)

        return rows

    @staticmethod
    def _find_card(heading):

        current = heading

        # Move upwards through the HTML until
        # we find the smallest parent containing
        # one IPO card.

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

            if "/ipo/" in url:

                if "check" not in url.lower():

                    return url

        return None

    def _parse_card(
        self,
        text,
        url,
        company,
        collected_at
    ):

        dates = re.search(
            r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})"
            r"\s*[–-]\s*"
            r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})",
            text
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
            r"(Live|Open|Closed|"
            r"Allotment Out|"
            r"Allotment Awaited|"
            r"Tentative Dates|"
            r"DRHP Approved)",
            text,
            re.I
        )

        # Ignore things that clearly aren't IPO cards.

        if not any(
            [
                price,
                lot,
                issue,
                dates
            ]
        ):
            return None

        return {

            "source": "ipoji",

            "source_id": (
                url.rstrip("/")
                .split("/")[-1]
            ),

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
                dates.group(1)
                if dates
                else None
            ),

            "close_date": (
                dates.group(2)
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

            "collected_at": collected_at,

            "raw": {
                "text": text
            }
        }


def num(value):

    cleaned = re.sub(
        r"[^0-9.\-]",
        "",
        str(value)
    )

    try:

        if "." in cleaned:
            return float(cleaned)

        return int(cleaned)

    except (
        TypeError,
        ValueError
    ):

        return None


def collect_once():

    client = IPOJiClient()

    rows = client.get_current_ipos()

    database = Database()

    try:

        count = database.upsert_ipos(
            rows
        )

    finally:

        database.close()

    return {
        "count": count,
        "rows": rows
    }


if __name__ == "__main__":

    result = collect_once()

    print(
        f"Collected and stored "
        f"{result['count']} IPOs."
    )

    for row in result["rows"]:

        print(
            f"- {row['company_name']} | "
            f"{row['status']} | "
            f"{row['price_low']}-"
            f"{row['price_high']}"
        )
